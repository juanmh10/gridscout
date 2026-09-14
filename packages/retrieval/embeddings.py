import os
import random
import hashlib
import logging
from typing import List, Optional, Protocol
from packages.ai.genai_auth import resolve_genai_auth_mode

logger = logging.getLogger(__name__)

class EmbeddingProvider(Protocol):
    def embed_text(self, text: str) -> List[float]:
        ...

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        ...

    def embed_query(self, query: str) -> List[float]:
        ...

class LocalHashEmbeddingProvider:
    """Deterministic local 768-dimensional embedding provider for test and local mode."""

    def __init__(self, dim: int = 768):
        self.dim = dim

    def embed_text(self, text: str) -> List[float]:
        return create_deterministic_embedding(text, dim=self.dim)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self.embed_text(t) for t in texts]

    def embed_query(self, query: str) -> List[float]:
        return self.embed_text(query)

class GeminiEmbeddingProvider:
    """Vertex AI / Gemini Embedding model adapter (e.g. text-embedding-004 / text-embedding-005)."""

    def __init__(
        self,
        project_id: Optional[str] = None,
        location: Optional[str] = None,
        api_key: Optional[str] = None,
        model: str = "text-embedding-004",
        dim: int = 768
    ):
        self.project_id = project_id or os.getenv("VERTEX_PROJECT_ID") or os.getenv("GCP_PROJECT_ID")
        self.location = location or os.getenv("VERTEX_LOCATION", "global")
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.auth_mode = os.getenv("GENAI_AUTH_MODE", "auto")
        self.model = model
        self.dim = dim
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from google import genai
            resolved_auth_mode = resolve_genai_auth_mode(
                project_id=self.project_id,
                api_key=self.api_key,
                auth_mode=self.auth_mode,
            )
            if resolved_auth_mode == "vertex":
                self._client = genai.Client(
                    vertexai=True,
                    project=self.project_id,
                    location=self.location
                )
            else:
                self._client = genai.Client(api_key=self.api_key)
            return self._client
        except Exception as e:
            logger.error(f"Failed to initialize Gemini embedding client: {e}")
            raise RuntimeError(f"Gemini embedding initialization error: {e}")

    def embed_text(self, text: str) -> List[float]:
        client = self._get_client()
        res = client.models.embed_content(
            model=self.model,
            contents=text,
            config={"output_dimensionality": self.dim}
        )
        return list(res.embeddings[0].values)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        client = self._get_client()
        res = client.models.embed_content(
            model=self.model,
            contents=texts,
            config={"output_dimensionality": self.dim}
        )
        return [list(emb.values) for emb in res.embeddings]

    def embed_query(self, query: str) -> List[float]:
        return self.embed_text(query)

def create_deterministic_embedding(text: str, dim: int = 768) -> List[float]:
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    rng = random.Random(int(h[:16], 16))
    vec = [rng.gauss(0, 1) for _ in range(dim)]
    norm = sum(x*x for x in vec) ** 0.5
    if norm == 0:
        return [0.0] * dim
    return [round(x / norm, 6) for x in vec]

def get_embedding_provider(provider_type: Optional[str] = None) -> EmbeddingProvider:
    pt = provider_type or os.getenv("EMBEDDING_PROVIDER", "local").lower()
    if pt in ["vertex", "vertex_ai", "gemini"]:
        return GeminiEmbeddingProvider()
    return LocalHashEmbeddingProvider()
