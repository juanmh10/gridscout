import pytest
from packages.ai.model_gateway import (
    DeterministicLocalModelGateway, VertexModelGateway, get_model_gateway,
    _coerce_normalized_product_response,
)
from packages.retrieval.embeddings import (
    LocalHashEmbeddingProvider, GeminiEmbeddingProvider, get_embedding_provider
)
from packages.retrieval.retriever import (
    PgVectorRetriever, VertexSearchRetriever, get_retriever
)
from packages.ai.genai_auth import resolve_genai_auth_mode

@pytest.mark.asyncio
async def test_local_model_gateway_normalization():
    gw = DeterministicLocalModelGateway()
    res = await gw.normalize_listing(
        title="Placa de Video EVGA RTX 3080 FTW3 10GB",
        description="Em excelente estado"
    )
    assert res.category == "gpu"
    assert res.brand == "NVIDIA"
    assert res.model == "RTX 3080"
    assert res.variant == "10GB"
    assert res.confidence >= 0.90


@pytest.mark.asyncio
async def test_local_model_gateway_page_triage_classifies_delivery_and_seller_signals():
    gw = DeterministicLocalModelGateway()
    res = await gw.normalize_listing(
        title="RTX 3080 com envio",
        description="Placa funcionando, com caixa e nota fiscal.",
        category_hint="gpu",
        listing_context={
            "delivery_text": "Envio disponível para todo o Brasil",
            "seller_verification": "VERIFIED",
            "seller_evidence": ["Usuário verificado"],
            "seller_rating": 4.9,
        },
    )
    assert res.delivery_status == "AVAILABLE"
    assert res.seller_verification == "VERIFIED"
    assert res.seller_signal_level == "HIGH"
    assert res.description_quality == "COMPLETE"
    assert res.schema_version in ("listing-analysis-v1", "hardware-schema-v2")


def test_card_only_response_coerces_provider_unknown_strings_without_claiming_page_evidence():
    result = _coerce_normalized_product_response(
        {
            "category": "Laptops",
            "brand": "Apple",
            "model": "MacBook Pro",
            "confidence": "0.99",
            "extracted_attributes": {"ram": "16GB"},
            "delivery_evidence": "UNKNOWN",
            "seller_evidence": "UNKNOWN",
            "listing_risk_flags": "UNKNOWN",
        },
        triage={},
        card_only=True,
    )
    assert result["category"] == "notebook"
    assert result["confidence"] == 0.99
    assert result["delivery_evidence"] == []
    assert result["seller_evidence"] == []
    assert result["delivery_status"] == "UNKNOWN"
    assert result["description_quality"] == "UNKNOWN"


def test_provider_product_type_outside_taxonomy_is_coerced_to_other():
    result = _coerce_normalized_product_response(
        {
            "category": "camera",
            "brand": "Canon",
            "model": "EOS Rebel",
            "confidence": "0.98",
        },
        triage={},
        card_only=False,
    )

    assert result["category"] == "other"
    assert result["classification_status"] == "unverified"
    assert result["confidence"] == 0.5
    assert "classificado como Outro" in result["classification_evidence"][0]

@pytest.mark.asyncio
async def test_local_model_gateway_investigation():
    gw = DeterministicLocalModelGateway()
    res = await gw.investigate_opportunity(
        listing_dict={"price": 2100.0, "location_city": "São Paulo", "location_state": "SP", "condition": "like_new"},
        product_dict={"id": "prod-gpu-rtx3080", "display_name": "NVIDIA GeForce RTX 3080 10GB"},
        market_stats={"estimated_clearing_value": 2700.0, "median": 2850.0, "sample_size": 25, "heat_band": "HOT", "confidence": 0.94}
    )
    assert res.status == "completed"
    assert res.verdict == "ATTRACTIVE_OPPORTUNITY"
    assert len(res.key_evidence) > 0
    assert res.schema_version == "listing-analysis-v1"
    assert res.stage == "deep_analysis"
    assert "delivery" in res.model_dump()
    assert res.page_scope["external_navigation_count"] == 0

def test_vertex_model_gateway_init_guard():
    # When unconfigured, trying to get client raises clean error rather than silent crash
    gw = VertexModelGateway(project_id=None, api_key=None)
    with pytest.raises(RuntimeError):
        gw._get_client()

def test_gemini_api_mode_wins_when_vertex_project_is_also_configured(monkeypatch):
    monkeypatch.setenv("GENAI_AUTH_MODE", "gemini_api")
    assert resolve_genai_auth_mode(
        project_id="test-project",
        api_key="test-key",
    ) == "gemini_api"

def test_gateway_builds_gemini_client_when_api_key_mode_is_explicit(monkeypatch):
    from google import genai

    calls = []

    def fake_client(**kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(genai, "Client", fake_client)
    gateway = VertexModelGateway(
        project_id="test-project",
        api_key="test-key",
        auth_mode="gemini_api",
    )

    gateway._get_client()

    assert calls == [{"api_key": "test-key"}]

def test_gateway_builds_vertex_client_when_adc_mode_is_explicit(monkeypatch):
    from google import genai

    calls = []

    def fake_client(**kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(genai, "Client", fake_client)
    gateway = VertexModelGateway(
        project_id="test-project",
        api_key="test-key",
        auth_mode="vertex",
    )

    gateway._get_client()

    assert calls == [{
        "vertexai": True,
        "project": "test-project",
        "location": "global",
    }]

def test_gemini_api_json_config_omits_unsupported_arbitrary_object_schema():
    gateway = VertexModelGateway(
        project_id="test-project",
        api_key="test-key",
        auth_mode="gemini_api",
    )
    gateway._resolved_auth_mode = "gemini_api"

    config = gateway._json_response_config(dict)

    assert config == {"response_mime_type": "application/json"}

def test_ambiguous_genai_credentials_fail_fast(monkeypatch):
    monkeypatch.delenv("GENAI_AUTH_MODE", raising=False)
    with pytest.raises(ValueError, match="GENAI_AUTH_MODE"):
        resolve_genai_auth_mode(project_id="test-project", api_key="test-key")

def test_vertex_mode_requires_adc_project(monkeypatch):
    monkeypatch.setenv("GENAI_AUTH_MODE", "vertex")
    with pytest.raises(ValueError, match="VERTEX_PROJECT_ID"):
        resolve_genai_auth_mode(project_id=None, api_key="test-key")

def test_vertex_adapters_read_location_from_env(monkeypatch):
    monkeypatch.setenv("VERTEX_LOCATION", "global")

    gateway = VertexModelGateway(project_id="test-project")
    embedding_provider = GeminiEmbeddingProvider(project_id="test-project")
    retriever = VertexSearchRetriever(project_id="test-project")

    assert gateway.location == "global"
    assert embedding_provider.location == "global"
    assert retriever.location == "global"

def test_gemini_embedding_provider_init_guard():
    ep = GeminiEmbeddingProvider(project_id=None, api_key=None)
    with pytest.raises(RuntimeError):
        ep._get_client()

def test_local_hash_embedding_provider():
    ep = LocalHashEmbeddingProvider(dim=768)
    vec = ep.embed_text("RTX 3080 thermal pad inspection")
    assert len(vec) == 768
    # Determinism
    vec2 = ep.embed_text("RTX 3080 thermal pad inspection")
    assert vec == vec2

def test_retriever_factory():
    local_ret = get_retriever("pgvector")
    assert isinstance(local_ret, PgVectorRetriever)

    vertex_ret = get_retriever("vertex_search")
    assert isinstance(vertex_ret, VertexSearchRetriever)


def test_agent_card_gate_result_coerces_string_evidence_and_literals():
    from packages.ai.model_gateway import AgentCardGateResult

    payload = {
        "verdict": "target_product",
        "primary_item_type": "console",
        "target_product": {
            "brand": "Sony",
            "model": "PlayStation 5",
            "variant": "825GB",
        },
        "confidence": 1.0,
        "reason_code": "CONSOLE_MATCH",
        "evidence": "Title explicitly states 'Playstation 5 825gb Midia Fisica'",
        "schema_version": "agent-card-gate-v1",
    }
    result = AgentCardGateResult.model_validate(payload)
    assert result.verdict == "target_product"
    assert result.primary_item_type == "console"
    assert result.target_product.brand == "Sony"
    assert result.target_product.model == "PlayStation 5"
    assert result.target_product.variant == "825GB"
    assert result.evidence == ["Title explicitly states 'Playstation 5 825gb Midia Fisica'"]
    assert result.confidence == 1.0

