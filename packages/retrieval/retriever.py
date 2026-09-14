import os
import logging
import numpy as np
from typing import Protocol, List, Dict, Any, Optional
from pydantic import BaseModel
from sqlalchemy.orm import Session
from packages.core.models import ProductKnowledge
from packages.retrieval.embeddings import get_embedding_provider

logger = logging.getLogger(__name__)

class RetrievalSearchResult(BaseModel):
    id: str
    product_id: Optional[str] = None
    category: str
    title: str
    body: str
    score: float
    metadata: Dict[str, Any] = {}

class Retriever(Protocol):
    async def search(
        self, 
        query: str, 
        category: Optional[str] = None, 
        limit: int = 5, 
        db: Optional[Session] = None
    ) -> List[RetrievalSearchResult]:
        ...

class PgVectorRetriever:
    """PostgreSQL pgvector similarity retriever (with in-memory fallback for SQLite tests)."""

    def __init__(self, embedding_provider=None):
        self.embedding_provider = embedding_provider or get_embedding_provider()

    async def search(
        self, 
        query: str, 
        category: Optional[str] = None, 
        limit: int = 5, 
        db: Optional[Session] = None
    ) -> List[RetrievalSearchResult]:
        if db is None:
            return []

        q = db.query(ProductKnowledge)
        if category:
            q = q.filter(ProductKnowledge.category == category)

        docs = q.all()
        if not docs:
            return []

        q_vec = np.array(self.embedding_provider.embed_query(query), dtype=float)
        results = []

        for d in docs:
            if d.embedding:
                doc_vec = np.array(d.embedding, dtype=float)
                norm_prod = np.linalg.norm(q_vec) * np.linalg.norm(doc_vec)
                cosine_sim = float(np.dot(q_vec, doc_vec) / norm_prod) if norm_prod > 0 else 0.5
            else:
                cosine_sim = 0.5

            # Keyword lexical match boost
            words = query.lower().split()
            kw_matches = sum(1 for w in words if w in d.title.lower() or w in d.body.lower())
            final_score = min(0.99, max(0.1, cosine_sim + (kw_matches * 0.15)))

            results.append(RetrievalSearchResult(
                id=d.id,
                product_id=d.product_id,
                category=d.category,
                title=d.title,
                body=d.body,
                score=round(final_score, 3),
                metadata=d.metadata_json or {}
            ))

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:limit]

class VertexSearchRetriever:
    """Vertex AI Search / Discovery Engine adapter for enterprise knowledge retrieval."""

    def __init__(
        self,
        project_id: Optional[str] = None,
        location: Optional[str] = None,
        data_store_id: Optional[str] = None
    ):
        self.project_id = project_id or os.getenv("VERTEX_PROJECT_ID") or os.getenv("GCP_PROJECT_ID")
        self.location = (
            location
            or os.getenv("VERTEX_SEARCH_LOCATION")
            or os.getenv("VERTEX_LOCATION", "global")
        )
        self.data_store_id = data_store_id or os.getenv("VERTEX_DATA_STORE_ID", "hardware-knowledge")
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from google.cloud import discoveryengine_v1 as discoveryengine
            self._client = discoveryengine.SearchServiceClient()
            return self._client
        except Exception as e:
            logger.error(f"Failed to initialize Vertex AI Search client: {e}")
            raise RuntimeError(f"Vertex AI Search initialization error: {e}")

    async def search(
        self, 
        query: str, 
        category: Optional[str] = None, 
        limit: int = 5, 
        db: Optional[Session] = None
    ) -> List[RetrievalSearchResult]:
        if not self.project_id:
            # Fall back to PgVectorRetriever if project not set
            fallback = PgVectorRetriever()
            return await fallback.search(query=query, category=category, limit=limit, db=db)

        try:
            from google.cloud import discoveryengine_v1 as discoveryengine
            client = self._get_client()
            serving_config = client.serving_config_path(
                project=self.project_id,
                location=self.location,
                data_store=self.data_store_id,
                serving_config="default_config",
            )
            request = discoveryengine.SearchRequest(
                serving_config=serving_config,
                query=query,
                page_size=limit,
            )
            response = client.search(request)
            
            results = []
            for r in response.results:
                data = r.document.derived_struct_data or {}
                results.append(RetrievalSearchResult(
                    id=r.document.id,
                    title=data.get("title", "Vertex Doc"),
                    body=data.get("snippet", ""),
                    category=category or "hardware",
                    score=0.90,
                    metadata={"source": "vertex_search"}
                ))
            return results
        except Exception as e:
            logger.warning(f"Vertex Search API call failed, falling back to local retriever: {e}")
            fallback = PgVectorRetriever()
            return await fallback.search(query=query, category=category, limit=limit, db=db)

def get_retriever(retriever_type: Optional[str] = None) -> Retriever:
    rt = retriever_type or os.getenv("RETRIEVER", "pgvector").lower()
    if rt in ["vertex", "vertex_search", "discovery_engine"]:
        return VertexSearchRetriever()
    return PgVectorRetriever()
