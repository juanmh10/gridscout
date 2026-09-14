import os
import json
import logging
import re
import base64
from typing import Literal, Protocol, List, Dict, Any, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator
from packages.ai.genai_auth import resolve_genai_auth_mode
from packages.ai.metrics import metric_started, record_model_metric
from packages.core.hardware_taxonomy import normalize_category
from packages.search.compiler import compile_search_intent as compile_search_intent_local
from packages.search.models import SearchIntentV1, SearchPlanV1

logger = logging.getLogger(__name__)

class NormalizedProductResult(BaseModel):
    product_id: Optional[str] = None
    category: str
    item_form: str = "standalone"
    classification_status: str = "unverified"
    brand: str
    model: str
    variant: Optional[str] = None
    confidence: float
    taxonomy_version: str = "hardware-taxonomy-v2"
    schema_version: str = "hardware-schema-v2"
    extracted_attributes: Dict[str, Any] = Field(default_factory=dict)
    classification_evidence: List[str] = Field(default_factory=list)
    delivery_status: str = "UNKNOWN"
    delivery_confidence: float = 0.0
    delivery_evidence: List[str] = Field(default_factory=list)
    seller_verification: str = "UNKNOWN"
    seller_signal_level: str = "UNKNOWN"
    seller_evidence: List[str] = Field(default_factory=list)
    listing_summary: str = ""
    description_quality: str = "UNKNOWN"
    listing_risk_flags: List[str] = Field(default_factory=list)

class InvestigationResult(BaseModel):
    status: str
    verdict: str
    confidence: float
    key_evidence: List[str]
    risks: List[str]
    market_summary: str
    suggested_next_checks: List[str]
    tool_trace: List[Dict[str, Any]]
    schema_version: str = "listing-analysis-v1"
    stage: str = "deep_analysis"
    model_id: str = "deterministic-local"
    page_scope: Dict[str, Any] = Field(default_factory=dict)
    delivery: Dict[str, Any] = Field(default_factory=dict)
    seller: Dict[str, Any] = Field(default_factory=dict)
    listing: Dict[str, Any] = Field(default_factory=dict)
    photos: Dict[str, Any] = Field(default_factory=dict)
    opportunity_assessment: Dict[str, Any] = Field(default_factory=dict)


class PipelineChatResult(BaseModel):
    """Text plus grounded sources returned by the dedicated chat operation."""

    content: str
    citations: List[Dict[str, str]] = Field(default_factory=list)


class AgentCardTargetProduct(BaseModel):
    """Identity emitted by the bounded, card-only semantic gate."""

    model_config = ConfigDict(extra="forbid", strict=True)

    brand: str
    model: str
    variant: str | None = None

    @field_validator("brand", "model", mode="before")
    @classmethod
    def _coerce_required_str(cls, v: Any) -> str:
        return str(v or "").strip()

    @field_validator("variant", mode="before")
    @classmethod
    def _coerce_optional_str(cls, v: Any) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None


class AgentCardGateResult(BaseModel):
    """Versioned contract used only for agent review of search cards."""

    model_config = ConfigDict(extra="forbid", strict=True)

    verdict: Literal["target_product", "exclude", "uncertain"]
    primary_item_type: Literal[
        "target_product", "console", "game", "accessory", "service", "wanted_ad", "box_only", "parts", "unrelated", "ambiguous", "pc_component", "gpu", "cpu", "notebook", "motherboard", "ram", "ssd", "smartphone", "tablet", "desktop", "monitor", "peripheral"
    ]
    target_product: AgentCardTargetProduct
    confidence: float = Field(ge=0.0, le=1.0)
    reason_code: str
    evidence: List[str]
    schema_version: Literal["agent-card-gate-v1"]

    @field_validator("verdict", "primary_item_type", mode="before")
    @classmethod
    def _coerce_literals(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @field_validator("reason_code", mode="before")
    @classmethod
    def _coerce_reason_code(cls, v: Any) -> str:
        return str(v or "UNKNOWN").strip()

    @field_validator("evidence", mode="before")
    @classmethod
    def _coerce_evidence(cls, v: Any) -> List[str]:
        if isinstance(v, str):
            return [v.strip()] if v.strip() else []
        if isinstance(v, (list, tuple)):
            return [str(item).strip() for item in v if str(item).strip()]
        return []


def _fold(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _delivery_from_context(context: Optional[Dict[str, Any]]) -> tuple[str, float, List[str]]:
    context = context or {}
    evidence = [str(item).strip() for item in context.get("delivery_evidence", []) if str(item).strip()]
    raw = " ".join(
        [
            str(context.get("delivery_text", "")),
            *evidence,
            *[str(item) for item in (context.get("attributes") or {}).values()],
        ]
    )
    text = _fold(raw)
    unavailable_markers = ("retirada", "somente local", "apenas retirada", "pickup_only", "não envio", "nao envio")
    available_markers = ("entrega disponível", "entrega disponivel", "envio disponível", "envio disponivel", "frete", "envio", "shipping")
    if any(marker in text for marker in unavailable_markers):
        return "UNAVAILABLE", 0.92, evidence or [raw[:240]]
    if any(marker in text for marker in available_markers):
        return "AVAILABLE", 0.88, evidence or [raw[:240]]
    return "UNKNOWN", 0.35 if raw else 0.0, evidence


def _triage_fields(title: str, description: str, context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    context = context or {}
    delivery_status, delivery_confidence, delivery_evidence = _delivery_from_context(context)
    description_text = re.sub(r"\s+", " ", description or "").strip()
    description_lower = _fold(description_text)
    seller_evidence = [str(item).strip() for item in context.get("seller_evidence", []) if str(item).strip()]
    seller_verification = str(context.get("seller_verification") or "UNKNOWN").upper()
    if seller_verification not in {"VERIFIED", "NOT_DISPLAYED", "UNKNOWN"}:
        seller_verification = "UNKNOWN"
    rating = context.get("seller_rating")
    try:
        rating_value = float(rating) if rating is not None else 0.0
    except (TypeError, ValueError):
        rating_value = 0.0
    if rating_value >= 4.7:
        seller_signal_level = "HIGH"
        seller_evidence = seller_evidence or [f"Avaliação pública exibida: {rating_value:.1f}"]
    elif rating_value >= 4.2:
        seller_signal_level = "MEDIUM"
        seller_evidence = seller_evidence or [f"Avaliação pública exibida: {rating_value:.1f}"]
    else:
        seller_signal_level = "UNKNOWN"
    risks = []
    if "whats" in description_lower or "whatsapp" in description_lower:
        risks.append("off_platform_contact")
    if len(description_text) < 24:
        quality = "INCOMPLETE"
    elif "defeito" in description_lower and "funcion" in description_lower:
        quality = "INCONSISTENT"
        risks.append("description_inconsistency")
    else:
        quality = "COMPLETE"
    summary_source = description_text.split(".", 1)[0].strip() if description_text else "Descrição não informada."
    summary = f"{title.strip()}. {summary_source}".strip()[:360]
    return {
        "schema_version": "listing-analysis-v1",
        "delivery_status": delivery_status,
        "delivery_confidence": round(delivery_confidence, 2),
        "delivery_evidence": delivery_evidence,
        "seller_verification": seller_verification,
        "seller_signal_level": seller_signal_level,
        "seller_evidence": seller_evidence,
        "listing_summary": summary,
        "description_quality": quality,
        "listing_risk_flags": risks,
    }


def _coerce_normalized_product_response(
    payload: Any,
    *,
    triage: Dict[str, Any],
    card_only: bool,
) -> Dict[str, Any]:
    """Make permissive Gemini JSON safe for the durable product contract.

    Gemini commonly represents an empty evidence list as the string
    ``"UNKNOWN"``. That should not discard an otherwise useful identity
    extraction. For search cards, page-only signals are always overwritten
    with explicit unknown values, regardless of what the model returned.
    """
    if not isinstance(payload, dict):
        raise ValueError("normalized_product_response_not_an_object")
    data = dict(payload)
    category = str(data.get("category") or "other").strip().lower()
    category_aliases = {
        "laptop": "notebook", "laptops": "notebook", "notebooks": "notebook",
        "computador portatil": "notebook", "computador portátil": "notebook",
        "graphics card": "gpu", "graphics cards": "gpu", "video card": "gpu",
        "placa de video": "gpu", "placa de vídeo": "gpu",
        "processor": "cpu", "processador": "cpu",
    }
    # Provider output is permissive, but the durable taxonomy is not: an
    # unrecognised product type belongs in the explicit ``other`` bucket.
    # This is especially important for broad collection, where a search hint
    # must never turn an unrelated item into an apparent match.
    data["category"] = normalize_category(category_aliases.get(category, category or "other")).value
    if data["category"] == "other":
        data["classification_status"] = "unverified"
        evidence = data.get("classification_evidence")
        if not isinstance(evidence, list):
            evidence = []
        if "Não se enquadra nas categorias de hardware cobertas; classificado como Outro." not in evidence:
            evidence.append("Não se enquadra nas categorias de hardware cobertas; classificado como Outro.")
        data["classification_evidence"] = evidence
    for key in ("brand", "model"):
        data[key] = str(data.get(key) or "").strip()
    try:
        data["confidence"] = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        data["confidence"] = 0.0
    if data["category"] == "other":
        data["confidence"] = min(0.5, data["confidence"])
    if not isinstance(data.get("extracted_attributes"), dict):
        data["extracted_attributes"] = {}
    for key in ("delivery_evidence", "seller_evidence", "listing_risk_flags"):
        value = data.get(key)
        if isinstance(value, list):
            data[key] = [str(item) for item in value if str(item).strip() and str(item).strip().upper() != "UNKNOWN"]
        elif value is None or str(value).strip().upper() in {"", "UNKNOWN", "N/A", "NONE"}:
            data[key] = []
        else:
            data[key] = [str(value).strip()]
    if card_only:
        data.update({
            "delivery_status": "UNKNOWN",
            "delivery_confidence": 0.0,
            "delivery_evidence": [],
            "seller_verification": "UNKNOWN",
            "seller_signal_level": "UNKNOWN",
            "seller_evidence": [],
            "description_quality": "UNKNOWN",
        })
    else:
        for key, value in triage.items():
            data.setdefault(key, value)
    return data


def _deep_defaults(
    listing_dict: Dict[str, Any],
    triage: Optional[Dict[str, Any]] = None,
    market_stats: Optional[Dict[str, Any]] = None,
    verdict: Optional[str] = None,
) -> Dict[str, Any]:
    triage = triage or _triage_fields(listing_dict.get("title", ""), listing_dict.get("description", ""), listing_dict)
    photos = listing_dict.get("photos") or []
    market_stats = market_stats or {}
    clearing = float(market_stats.get("estimated_clearing_value", 0.0) or 0.0)
    price = float(listing_dict.get("price", 0.0) or 0.0)
    edge = (clearing - price) / clearing if clearing else 0.0
    return {
        "schema_version": "listing-analysis-v1",
        "stage": "deep_analysis",
        "model_id": "deterministic-local",
        "page_scope": {
            "source_url": listing_dict.get("source_url", ""),
            "external_navigation_count": 0,
            "photos_examined": min(len(photos), 5),
        },
        "delivery": {
            "status": triage.get("delivery_status", "UNKNOWN"),
            "confidence": triage.get("delivery_confidence", 0.0),
            "evidence": triage.get("delivery_evidence", []),
        },
        "seller": {
            "verification": triage.get("seller_verification", "UNKNOWN"),
            "signal_level": triage.get("seller_signal_level", "UNKNOWN"),
            "evidence": triage.get("seller_evidence", []),
            "risk_flags": [],
        },
        "listing": {
            "nl_summary": triage.get("listing_summary", ""),
            "description_quality": triage.get("description_quality", "UNKNOWN"),
            "condition_assessment": listing_dict.get("condition", "UNKNOWN"),
            "condition_evidence": [],
            "risk_flags": triage.get("listing_risk_flags", []),
        },
        "photos": {
            "status": "ANALYZED" if photos else "NOT_AVAILABLE",
            "findings": ["Fotos capturadas na página original; análise visual local simulada."] if photos else [],
            "limitations": ["A análise não confirma funcionamento, autenticidade ou estado interno."],
        },
        "opportunity_assessment": {
            "verdict": verdict or ("ATTRACTIVE_OPPORTUNITY" if edge >= 0.15 else "FAIR_MARKET_PRICE" if edge >= 0 else "OVERPRICED"),
            "price_assessment": f"Preço pedido com vantagem de {edge * 100:.1f}% sobre o valor de liquidação estimado de R$ {clearing:.2f}.",
            "contact_recommended": True,
            "contact_reason": "Confirmar entrega, funcionamento e documentação diretamente no anúncio original.",
            "suggested_next_checks": [
                "Confirmar disponibilidade da entrega com o vendedor",
                "Solicitar vídeo funcional e comprovante de compra",
            ],
        },
    }

class ModelGateway(Protocol):
    async def compile_search_intent(self, text: str, current_intent: Optional[Dict[str, Any]] = None) -> SearchPlanV1:
        ...

    async def normalize_listing(
        self, 
        title: str, 
        description: str, 
        category_hint: Optional[str] = None,
        listing_context: Optional[Dict[str, Any]] = None,
    ) -> NormalizedProductResult:
        ...

    async def gate_card(
        self,
        title: str,
        *,
        card_context: Dict[str, Any],
    ) -> AgentCardGateResult:
        ...

    async def investigate_opportunity(
        self,
        listing_dict: Dict[str, Any],
        product_dict: Dict[str, Any],
        market_stats: Dict[str, Any],
        knowledge_snippets: Optional[List[Dict[str, Any]]] = None,
        tools_executor: Optional[Any] = None
    ) -> InvestigationResult:
        ...

    async def analyze_pipeline_chat(
        self,
        prompt: str,
        *,
        allow_web_search: bool = False,
        operation: str = "chat",
    ) -> PipelineChatResult:
        ...

class DeterministicLocalModelGateway:
    """Deterministic local simulated model gateway for local test and synthetic MVP mode."""

    def __init__(self):
        self.mode = "local"

    async def compile_search_intent(
        self,
        text: str,
        current_intent: Optional[Dict[str, Any]] = None,
    ) -> SearchPlanV1:
        """Compile the complete plan locally; no provider credentials needed."""
        if current_intent:
            from packages.search.service import compile_search

            return compile_search(text, current_intent=current_intent).plan
        return compile_search_intent_local(text)

    async def compile_search(self, text: str, current_intent: Optional[Dict[str, Any]] = None) -> SearchPlanV1:
        return await self.compile_search_intent(text, current_intent=current_intent)

    async def normalize_listing(
        self, 
        title: str, 
        description: str, 
        category_hint: Optional[str] = None,
        listing_context: Optional[Dict[str, Any]] = None,
    ) -> NormalizedProductResult:
        from packages.classification.classifier import classify_listing_text

        started_at, started_monotonic = metric_started()
        classification = classify_listing_text(
            title=title,
            description=description,
            category_hint=category_hint,
            listing_context=listing_context,
        )

        result = NormalizedProductResult(
            product_id=None,
            category=classification.category.value,
            item_form=classification.item_form.value,
            classification_status=classification.status.value,
            brand=classification.brand,
            model=classification.model,
            variant=classification.variant,
            confidence=classification.confidence,
            taxonomy_version=classification.taxonomy_version,
            schema_version=classification.schema_version,
            extracted_attributes=classification.attributes,
            classification_evidence=classification.evidence,
            delivery_status=classification.delivery_status,
            delivery_confidence=classification.delivery_confidence,
            delivery_evidence=classification.delivery_evidence,
            seller_verification=classification.seller_verification,
            seller_signal_level=classification.seller_signal_level,
            seller_evidence=classification.seller_evidence,
            listing_summary=classification.listing_summary,
            description_quality=classification.description_quality,
            listing_risk_flags=classification.listing_risk_flags,
        )

        record_model_metric(
            operation="normalize", provider="local", auth_mode="local", model_id="deterministic-local",
            started_at=started_at, started_monotonic=started_monotonic,
        )
        return result

    async def gate_card(
        self,
        title: str,
        *,
        card_context: Dict[str, Any],
    ) -> AgentCardGateResult:
        """Local test-only implementation; required mode rejects this gateway."""
        from packages.classification.classifier import classify_listing_text

        classification = classify_listing_text(title=title, description="")
        item_form = classification.item_form.value
        excluded = item_form in {"accessory", "parts"}
        return AgentCardGateResult(
            verdict="exclude" if excluded else "target_product" if classification.status.value == "confirmed" else "uncertain",
            primary_item_type="accessory" if item_form == "accessory" else "parts" if item_form == "parts" else "console" if "ps" in _fold(title) or "xbox" in _fold(title) else "ambiguous",
            target_product=AgentCardTargetProduct(
                brand=classification.brand or "",
                model=classification.model or "",
                variant=classification.variant,
            ),
            confidence=float(classification.confidence),
            reason_code="LOCAL_CARD_GATE",
            evidence=list(classification.evidence or []),
            schema_version="agent-card-gate-v1",
        )

    async def investigate_opportunity(
        self,
        listing_dict: Dict[str, Any],
        product_dict: Dict[str, Any],
        market_stats: Dict[str, Any],
        knowledge_snippets: Optional[List[Dict[str, Any]]] = None,
        tools_executor: Optional[Any] = None
    ) -> InvestigationResult:
        started_at, started_monotonic = metric_started()
        from packages.market.engine import run_simulated_investigation
        res = run_simulated_investigation(
            listing_dict=listing_dict,
            product_dict=product_dict,
            market_stats=market_stats,
            knowledge_snippets=knowledge_snippets
        )
        record_model_metric(
            operation="investigate", provider="local", auth_mode="local", model_id="deterministic-local",
            started_at=started_at, started_monotonic=started_monotonic,
        )
        res.update(_deep_defaults(listing_dict, market_stats=market_stats, verdict=res.get("verdict")))
        return InvestigationResult(**res)

    async def analyze_pipeline_chat(
        self,
        prompt: str,
        *,
        allow_web_search: bool = False,
        operation: str = "chat",
    ) -> PipelineChatResult:
        raise RuntimeError("MODEL_PROVIDER_DISABLED")

class VertexModelGateway:
    """Production Vertex AI / Gemini model gateway with structured outputs and tool calling."""

    def __init__(
        self,
        project_id: Optional[str] = None,
        location: Optional[str] = None,
        api_key: Optional[str] = None,
        model_extraction: Optional[str] = None,
        model_investigation: Optional[str] = None,
        model_chat: Optional[str] = None,
        model_pro: Optional[str] = None,
        auth_mode: Optional[str] = None,
    ):
        self.project_id = project_id or os.getenv("VERTEX_PROJECT_ID") or os.getenv("GCP_PROJECT_ID")
        self.location = location or os.getenv("VERTEX_LOCATION", "global")
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.auth_mode = auth_mode or os.getenv("GENAI_AUTH_MODE", "auto")
        self.model_extraction = model_extraction or os.getenv("VERTEX_MODEL_EXTRACTION", "gemini-3.5-flash-lite")
        self.model_investigation = model_investigation or os.getenv("VERTEX_MODEL_INVESTIGATION", "gemini-3.7-flash")
        self.model_chat = model_chat or os.getenv("VERTEX_MODEL_CHAT", self.model_investigation)
        self.model_pro = model_pro or os.getenv("VERTEX_MODEL_PRO", "gemini-3.1-pro")
        self._client = None
        self._resolved_auth_mode = None

    async def compile_search_intent(
        self,
        text: str,
        current_intent: Optional[Dict[str, Any]] = None,
    ) -> SearchPlanV1:
        """Ask Gemini for a complete plan and validate it atomically.

        If a provider returns malformed or partial JSON, the entire response is
        discarded and the deterministic compiler is used.  No partially parsed
        intent or plan can escape this method.
        """

        started_at, started_monotonic = metric_started()
        response = None
        current_payload = current_intent.model_dump(mode="json") if hasattr(current_intent, "model_dump") else (current_intent or {})
        prompt = f"""
Compile this product search request into a complete SearchPlanV1 JSON object.
Request: {text}
Previous intent (may be empty): {json.dumps(current_payload, ensure_ascii=False)}
Return schema_version='search-plan-v1', an intent with schema_version='search-intent-v1',
must/should/must_not criteria, up to 3 primary and 3 fallback queries, assumptions,
clarifications, taxonomy_version, desired_count, max_results, scope_mode,
native_filters and marketplace_review. scope_mode is broad only when the user
explicitly requests broad inventory, maximum volume, or filtering later; otherwise
it is precise. marketplace_review must explain each mandatory criterion in marketplace
terms, identify card or detail evidence, and use missing_policy='unverified'. Use only typed
operators eq, in, gte, lte, range, equivalent_or_better. Never omit required
objects or return a partial object. The OLX browser search uses a category page
and short title-indexable terms: make each retrieval query concise (product,
model/brand and at most one title-visible feature such as IPS). Keep price and
Wi-Fi-band requirements in typed criteria for card/detail validation; do not
put the full conversational sentence or a Wi-Fi-only phrase in a query.
"""
        try:
            client = self._get_client()
            response = client.models.generate_content(
                model=self.model_extraction,
                contents=prompt,
                config=self._json_response_config(SearchPlanV1),
            )
            data = json.loads(getattr(response, "text", ""))
            required_plan_keys = {
                "schema_version", "intent", "must", "should", "must_not",
                "primary_queries", "fallback_queries", "assumptions", "clarifications",
                "desired_count", "max_results",
            }
            required_intent_keys = {
                "schema_version", "category", "brands", "families", "models",
                "generations", "budget", "location", "condition", "desired_count",
                "must", "should", "must_not",
            }
            if not isinstance(data, dict) or not required_plan_keys.issubset(data):
                raise ValueError("partial_search_plan_response")
            if not isinstance(data.get("intent"), dict) or not required_intent_keys.issubset(data["intent"]):
                raise ValueError("partial_search_intent_response")
            # Validation of the complete root object is intentionally a single
            # operation; nested partial objects are not accepted.
            plan = SearchPlanV1.model_validate(data)
            record_model_metric(
                operation="compile_search_intent", provider="gemini",
                auth_mode=self._resolved_auth_mode or self.auth_mode,
                model_id=self.model_extraction, started_at=started_at,
                started_monotonic=started_monotonic, response=response,
            )
            return plan
        except Exception as exc:
            record_model_metric(
                operation="compile_search_intent", provider="gemini",
                auth_mode=self._resolved_auth_mode or self.auth_mode,
                model_id=self.model_extraction, started_at=started_at,
                started_monotonic=started_monotonic, response=response,
                status="fallback", error_code=type(exc).__name__,
            )
            # Deterministic fallback is a complete plan, not a partial model
            # response.  It is safe and inspectable in local/test mode.
            if current_intent:
                from packages.search.service import compile_search

                return compile_search(text, current_intent=current_intent).plan
            return compile_search_intent_local(text)

    async def compile_search(self, text: str, current_intent: Optional[Dict[str, Any]] = None) -> SearchPlanV1:
        return await self.compile_search_intent(text, current_intent=current_intent)

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
            self._resolved_auth_mode = resolved_auth_mode
            if resolved_auth_mode == "vertex":
                # Vertex AI client mode
                self._client = genai.Client(
                    vertexai=True,
                    project=self.project_id,
                    location=self.location
                )
            else:
                # Gemini Developer API client mode
                self._client = genai.Client(api_key=self.api_key)
            return self._client
        except Exception as e:
            logger.error(f"Failed to initialize Vertex AI client: {e}")
            raise RuntimeError(f"Vertex AI initialization error: {e}")

    def _json_response_config(self, schema):
        """Build a response config supported by the selected GenAI backend.

        Gemini Developer API does not accept the arbitrary-object
        ``additionalProperties`` emitted by our Dict[str, Any] fields. The
        prompt still requests the same JSON contract, while Vertex keeps the
        stricter typed schema support.
        """
        config = {"response_mime_type": "application/json"}
        if self._resolved_auth_mode == "vertex":
            config["response_schema"] = schema
        return config

    async def normalize_listing(
        self, 
        title: str, 
        description: str, 
        category_hint: Optional[str] = None,
        listing_context: Optional[Dict[str, Any]] = None,
    ) -> NormalizedProductResult:
        started_at, started_monotonic = metric_started()
        try:
            client = self._get_client()
            triage = _triage_fields(title, description, listing_context)
            card_only = (listing_context or {}).get("evidence_scope") == "search_card_only"
            evidence_instruction = (
                "This is SEARCH-CARD-ONLY evidence, not a listing detail page. "
                "You may identify category, brand, model, variant and explicit title/card specs only. "
                "Never infer seller, delivery, condition, availability, authenticity, functioning or an opportunity verdict. "
                "Keep missing page-only fields UNKNOWN and explain uncertainty in listing_summary."
                if card_only else
                "This is listing-page evidence. Use only evidence present on this page. Do not open or infer anything from seller profiles or other links."
            )
            prompt = f"""
You are a hardware identification expert. Extract the canonical product identity from this marketplace listing.
Title: {title}
Description: {description}
Category Hint: {category_hint or "hardware"}
Evidence: {json.dumps({key: value for key, value in (listing_context or {}).items() if key != "photos"}, ensure_ascii=False)}
Evidence policy: {evidence_instruction}

Return JSON conforming to:
- category (string: gpu, notebook, cpu, ram, ssd, motherboard, other)
- brand (string)
- model (string)
- variant (string or null)
- confidence (float between 0.0 and 1.0)
- extracted_attributes (key-value dictionary of specs)
- delivery_status (AVAILABLE, UNAVAILABLE, or UNKNOWN), delivery_confidence, delivery_evidence
- seller_verification (VERIFIED, NOT_DISPLAYED, or UNKNOWN), seller_signal_level, seller_evidence
- listing_summary, description_quality (COMPLETE, INCOMPLETE, INCONSISTENT, or UNKNOWN), listing_risk_flags
{evidence_instruction}
"""
            response = client.models.generate_content(
                model=self.model_extraction,
                contents=prompt,
                config=self._json_response_config(NormalizedProductResult)
            )
        except Exception as exc:
            record_model_metric(
                operation="normalize", provider="gemini", auth_mode=self._resolved_auth_mode or self.auth_mode,
                model_id=self.model_extraction, started_at=started_at, started_monotonic=started_monotonic,
                status="failed", error_code=type(exc).__name__,
            )
            raise
        try:
            data = json.loads(response.text)
            result = NormalizedProductResult(**_coerce_normalized_product_response(
                data,
                triage=triage,
                card_only=card_only,
            ))
            metric_status, error_code = "success", None
        except Exception:
            fallback_category = normalize_category(category_hint).value if category_hint else "other"
            result = NormalizedProductResult(
                category=fallback_category, brand="Extracted", model=title[:30], confidence=0.75, **triage
            )
            metric_status, error_code = "fallback", "model_response_parse_error"
        record_model_metric(
            operation="normalize", provider="gemini", auth_mode=self._resolved_auth_mode or self.auth_mode,
            model_id=self.model_extraction, started_at=started_at, started_monotonic=started_monotonic,
            response=response, status=metric_status, error_code=error_code,
        )
        return result

    async def gate_card(
        self,
        title: str,
        *,
        card_context: Dict[str, Any],
    ) -> AgentCardGateResult:
        """Classify the primary item using only already-validated card evidence.

        Unlike generic listing normalization, this method has no permissive
        fallback.  A malformed provider response is an invalid gate decision
        and callers in required mode must retain the card.
        """
        started_at, started_monotonic = metric_started()
        response = None
        try:
            client = self._get_client()
            target_category = card_context.get("target_category", "hardware")
            expected_item_form = card_context.get("expected_item_form", "standalone")
            prompt = f"""
You are the final semantic gate for a marketplace SEARCH CARD. Return only a
JSON object matching agent-card-gate-v1 exactly. Determine the PRIMARY item
being advertised. Target category is {target_category} and expected item form is {expected_item_form}. A valid target product bundled with games or accessories remains a valid target_product; a game, accessory, service, wanted/buyer advertisement, empty box, part, scrap or unrelated product is not the target product unless specifically matching the target category (e.g., if searching for a GPU, a GPU is the target product, not a 'part' or 'accessory').

Use only the title and the validated visible card evidence below. Never infer
or repair a missing price. Return verdict='uncertain' when the evidence does
not support a confident decision. target_product must be present with empty
strings/null when unknown. Confidence is 0 through 1.

Title: {title}
Validated card evidence: {json.dumps(card_context, ensure_ascii=False)}

Required JSON fields:
- verdict: "target_product" | "exclude" | "uncertain"
- primary_item_type: "target_product" | "console" | "game" | "accessory" | "service" | "wanted_ad" | "box_only" | "parts" | "unrelated" | "ambiguous" | "pc_component" | "gpu" | "cpu" | "notebook" | "motherboard" | "ram" | "ssd" | "smartphone" | "tablet" | "desktop" | "monitor" | "peripheral"
- target_product: {{"brand": "...", "model": "...", "variant": "..."}}
- confidence: number between 0.0 and 1.0
- reason_code: "..."
- evidence: ["evidence item 1", ...]
- schema_version: "agent-card-gate-v1"
"""
            response = client.models.generate_content(
                model=self.model_extraction,
                contents=prompt,
                config=self._json_response_config(AgentCardGateResult),
            )
            payload = json.loads(getattr(response, "text", ""))
            result = AgentCardGateResult.model_validate(payload)
        except Exception as exc:
            record_model_metric(
                operation="card_gate", provider="gemini", auth_mode=self._resolved_auth_mode or self.auth_mode,
                model_id=self.model_extraction, started_at=started_at, started_monotonic=started_monotonic,
                response=response, status="failed", error_code=type(exc).__name__,
            )
            raise
        record_model_metric(
            operation="card_gate", provider="gemini", auth_mode=self._resolved_auth_mode or self.auth_mode,
            model_id=self.model_extraction, started_at=started_at, started_monotonic=started_monotonic,
            response=response,
        )
        return result

    async def investigate_opportunity(
        self,
        listing_dict: Dict[str, Any],
        product_dict: Dict[str, Any],
        market_stats: Dict[str, Any],
        knowledge_snippets: Optional[List[Dict[str, Any]]] = None,
        tools_executor: Optional[Any] = None
    ) -> InvestigationResult:
        started_at, started_monotonic = metric_started()
        try:
            client = self._get_client()
            photos = listing_dict.get("photos") or []
            listing_prompt = {key: value for key, value in listing_dict.items() if key != "photos"}
            prompt = f"""
You are an expert hardware appraiser analyzing an opportunity.
Listing page data: {json.dumps(listing_prompt, ensure_ascii=False)}
Photos examined on the original listing page: {len(photos)}
Canonical Product: {json.dumps(product_dict)}
Market Statistics: {json.dumps(market_stats)}
Diagnostic Knowledge: {json.dumps(knowledge_snippets or [])}

Evaluate the opportunity, price edge, liquidity, and physical inspection risks.
Return a structured appraisal with verdict (ATTRACTIVE_OPPORTUNITY, FAIR_MARKET_PRICE, or OVERPRICED),
confidence, key_evidence, risks, market_summary, and suggested_next_checks.
Also return page_scope, delivery, seller, listing, photos and opportunity_assessment using schema listing-analysis-v1.
Only use the original listing page data and supplied photos. Never follow links or inspect a seller profile.
"""
            contents: Any = prompt
            if photos:
                try:
                    from google.genai import types
                    parts: list[Any] = [prompt]
                    for photo in photos[:5]:
                        data_url = str(photo.get("data_url", ""))
                        if ";base64," not in data_url:
                            continue
                        header, encoded = data_url.split(",", 1)
                        mime_type = header.split(";", 1)[0].split(":", 1)[-1] or "image/jpeg"
                        parts.append(types.Part.from_bytes(data=base64.b64decode(encoded), mime_type=mime_type))
                    contents = parts
                except (ValueError, TypeError, base64.binascii.Error):
                    contents = prompt
            response = client.models.generate_content(
                model=self.model_investigation,
                contents=contents,
                config=self._json_response_config(InvestigationResult)
            )
        except Exception as exc:
            record_model_metric(
                operation="investigate", provider="gemini", auth_mode=self._resolved_auth_mode or self.auth_mode,
                model_id=self.model_investigation, started_at=started_at, started_monotonic=started_monotonic,
                status="failed", error_code=type(exc).__name__,
            )
            raise
        try:
            data = json.loads(response.text)
            result = InvestigationResult(**data)
            metric_status, error_code = "success", None
        except Exception:
            from packages.market.engine import run_simulated_investigation
            fallback = run_simulated_investigation(
                listing_dict=listing_dict,
                product_dict=product_dict,
                market_stats=market_stats,
                knowledge_snippets=knowledge_snippets
            )
            fallback.update(_deep_defaults(listing_dict, market_stats=market_stats, verdict=fallback.get("verdict")))
            result = InvestigationResult(**fallback)
            metric_status, error_code = "fallback", "model_response_parse_error"
        record_model_metric(
            operation="investigate", provider="gemini", auth_mode=self._resolved_auth_mode or self.auth_mode,
            model_id=self.model_investigation, started_at=started_at, started_monotonic=started_monotonic,
            response=response, status=metric_status, error_code=error_code,
        )
        return result

    @staticmethod
    def _grounding_citations(response: Any) -> List[Dict[str, str]]:
        """Extract only UI-safe source labels and URLs from Gemini grounding."""
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return []
        metadata = getattr(candidates[0], "grounding_metadata", None)
        chunks = getattr(metadata, "grounding_chunks", None) or []
        citations: List[Dict[str, str]] = []
        seen: set[str] = set()
        for chunk in chunks:
            web = getattr(chunk, "web", None)
            if web is None and isinstance(chunk, dict):
                web = chunk.get("web") or {}
            uri = getattr(web, "uri", None) if web is not None else None
            title = getattr(web, "title", None) if web is not None else None
            if isinstance(web, dict):
                uri = uri or web.get("uri")
                title = title or web.get("title")
            if not uri or uri in seen:
                continue
            seen.add(str(uri))
            citations.append({"title": str(title or uri), "url": str(uri)})
        return citations

    async def analyze_pipeline_chat(
        self,
        prompt: str,
        *,
        allow_web_search: bool = False,
        operation: str = "chat",
    ) -> PipelineChatResult:
        """Run the chat-only Gemini operation with no callable application tools."""
        started_at, started_monotonic = metric_started()
        response = None
        try:
            client = self._get_client()
            if allow_web_search:
                from google.genai import types
                config: Any = types.GenerateContentConfig(
                    temperature=0.2,
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                )
            else:
                config = {"temperature": 0.2}
            response = client.models.generate_content(
                model=self.model_chat,
                contents=prompt,
                config=config,
            )
            result = PipelineChatResult(
                content=(getattr(response, "text", None) or "Não foi possível gerar uma análise agora.").strip(),
                citations=self._grounding_citations(response) if allow_web_search else [],
            )
        except Exception as exc:
            record_model_metric(
                operation=operation,
                provider="gemini",
                auth_mode=self._resolved_auth_mode or self.auth_mode,
                model_id=self.model_chat,
                started_at=started_at,
                started_monotonic=started_monotonic,
                response=response,
                status="failed",
                error_code=type(exc).__name__,
            )
            raise
        record_model_metric(
            operation=operation,
            provider="gemini",
            auth_mode=self._resolved_auth_mode or self.auth_mode,
            model_id=self.model_chat,
            started_at=started_at,
            started_monotonic=started_monotonic,
            response=response,
        )
        return result

def get_model_gateway(gateway_type: Optional[str] = None) -> ModelGateway:
    gt = gateway_type or os.getenv("MODEL_GATEWAY", "local").lower()
    if gt in ["vertex", "vertex_ai", "gemini"]:
        return VertexModelGateway()
    return DeterministicLocalModelGateway()
