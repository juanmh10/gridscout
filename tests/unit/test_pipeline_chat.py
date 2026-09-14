import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from packages.ai.model_gateway import DeterministicLocalModelGateway, PipelineChatResult
from packages.core.database import Base
from packages.core.models import (
    Listing,
    ListingDiscovery,
    ListingSnapshot,
    MarketSnapshot,
    Opportunity,
    OpportunityEvaluation,
    OpportunitySignal,
    PipelineRun,
    Profile,
    Product,
)
from packages.pipeline.chat import PipelineChatError, PipelineChatService


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed_run(db, *, status="completed", error_code=None, count=12):
    profile = Profile(
        id="profile-chat",
        name="Chat",
        name_normalized="chat",
        preferences={},
    )
    run = PipelineRun(
        id="pipe-chat",
        profile_id=profile.id,
        type="olx_ingest" if error_code else "fixture_ingest",
        status=status,
        error_code=error_code,
        finished_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
    )
    product = Product(
        id="prod-chat", category="gpu", brand="NVIDIA", family="RTX", model="RTX 3080",
        display_name="NVIDIA RTX 3080",
    )
    db.add_all([profile, run, product])
    db.flush()
    db.add(MarketSnapshot(
        product_id=product.id, pipeline_run_id=run.id, estimated_clearing_value=2500,
        asking_median=2600, sample_size=count,
    ))
    for index in range(count):
        listing = Listing(
            id=f"listing-{index}", source="fixture", external_id=f"external-{index}", product_id=product.id,
            title=f"RTX 3080 lote {index}", description="Em bom estado e funcionando.", price=1800 + index,
            condition="good", source_url=f"https://example.test/{index}",
        )
        opportunity = Opportunity(
            id=f"opp-{index}", listing_id=listing.id, product_id=product.id,
        )
        evaluation = OpportunityEvaluation(
            opportunity_id=opportunity.id, pipeline_run_id=run.id, listing_id=listing.id, product_id=product.id,
            final_score=float(100 - index), price_edge=float(20 - index) / 100,
            status="qualified" if index < 4 else "not_qualified",
        )
        db.add_all([listing, opportunity, evaluation])
    db.commit()
    return run


@pytest.mark.asyncio
async def test_local_chat_batches_are_persisted_and_idempotent():
    db = _session()
    _seed_run(db)
    service = PipelineChatService(db, gateway=DeterministicLocalModelGateway())

    initial = service.get_chat("pipe-chat")
    assert initial["eligibility"] == {"available": True, "mode": "local"}
    assert initial["thread"] is None
    assert {item["action"] for item in initial["available_actions"]} == {
        "overview", "opportunity_summary", "rejection_summary", "price_conditions", "next_batch",
    }

    first = await service.send_message("pipe-chat", client_request_id="batch-1", action="next_batch")
    assert len(first["message"]["candidates"]) == 10
    assert first["message"]["candidates"][0]["final_score"] == 100.0
    assert first["thread"]["next_offset"] == 10
    assert first["message"]["metadata"] == {"offset": 0, "returned": 10, "total_candidates": 12}

    replay = await service.send_message("pipe-chat", client_request_id="batch-1", action="next_batch")
    assert replay["message"]["id"] == first["message"]["id"]
    assert replay["thread"]["next_offset"] == 10

    second = await service.send_message("pipe-chat", client_request_id="batch-2", action="next_batch")
    assert len(second["message"]["candidates"]) == 2
    assert second["thread"]["next_offset"] == 12
    assert not any(item["action"] == "next_batch" for item in second["available_actions"])


@pytest.mark.asyncio
async def test_internal_failure_never_creates_or_invokes_chat():
    db = _session()
    _seed_run(db, status="failed", error_code="RuntimeError", count=1)
    service = PipelineChatService(db, gateway=DeterministicLocalModelGateway())

    result = service.get_chat("pipe-chat")
    assert result["eligibility"]["available"] is False
    assert result["eligibility"]["reason_code"] == "PIPELINE_INTERNAL_FAILURE"
    assert result["thread"] is None

    with pytest.raises(PipelineChatError) as exc_info:
        await service.send_message("pipe-chat", client_request_id="blocked", action="overview")
    assert exc_info.value.code == "PIPELINE_CHAT_UNAVAILABLE"
    assert db.query(PipelineRun).first().chat_thread is None


@pytest.mark.asyncio
async def test_external_failure_is_safely_explainable_in_local_mode():
    db = _session()
    _seed_run(db, status="blocked", error_code="olx_access_blocked", count=0)
    service = PipelineChatService(db, gateway=DeterministicLocalModelGateway())

    result = await service.send_message("pipe-chat", client_request_id="overview", action="overview")
    assert "OLX bloqueou" in result["message"]["content"]
    assert "RuntimeError" not in result["message"]["content"]
    with pytest.raises(PipelineChatError) as exc_info:
        await service.send_message("pipe-chat", client_request_id="ask", action="ask", text="Por quê?")
    assert exc_info.value.code == "MODEL_PROVIDER_DISABLED"


class _GeminiGateway:
    mode = "gemini"

    async def analyze_pipeline_chat(self, prompt, *, allow_web_search=False, operation="chat"):
        assert "conteúdo não confiável" in prompt
        return PipelineChatResult(
            content="Análise externa concluída.",
            citations=[{"title": "Fonte", "url": "https://example.test/source"}] if allow_web_search else [],
        )


@pytest.mark.asyncio
async def test_market_check_uses_primary_pipeline_products_and_returns_citations():
    db = _session()
    _seed_run(db, count=1)
    service = PipelineChatService(db, gateway=_GeminiGateway())

    response = await service.send_message(
        "pipe-chat", client_request_id="market-1", action="market_check",
    )
    assert response["message"]["kind"] == "market_check"
    assert response["message"]["citations"] == [{"title": "Fonte", "url": "https://example.test/source"}]
    assert response["message"]["metadata"]["web_search"] is True
    assert response["message"]["metadata"]["market_scope"] == "pipeline"
    assert response["message"]["metadata"]["products_analyzed"] == [{"id": "prod-chat", "display_name": "NVIDIA RTX 3080"}]


@pytest.mark.asyncio
async def test_high_volume_chat_candidates_fallback():
    db = _session()
    profile = Profile(id="profile-hv-chat", name="HV Chat", name_normalized="hv chat", preferences={})
    run = PipelineRun(
        id="pipe-hv-chat",
        profile_id=profile.id,
        type="olx_high_volume",
        status="completed",
        finished_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
    )
    product = Product(
        id="prod-ps5", category="other", brand="Sony", family="PlayStation", model="PlayStation 5",
        display_name="Sony PlayStation 5",
    )
    db.add_all([profile, run, product])
    db.flush()

    ms = MarketSnapshot(
        product_id=product.id, pipeline_run_id=run.id, estimated_clearing_value=3400.0,
        asking_median=3500.0, sample_size=10,
    )
    db.add(ms)

    # Add 2 listings with snapshots and signals, without OpportunityEvaluation
    for idx, (price, title) in enumerate([(1500.0, "PS5 urgente"), (3200.0, "PS5 seminovo")]):
        listing = Listing(
            id=f"listing-hv-{idx}", source="olx", external_id=f"olx-ext-{idx}", product_id=product.id,
            title=title, description="Console em perfeito estado.", price=price, condition="good",
            source_url=f"https://www.olx.com.br/vi/12345{idx}",
        )
        discovery = ListingDiscovery(
            id=f"disc-hv-{idx}", pipeline_run_id=run.id, canonical_key=f"canonical-{idx}", external_id=listing.external_id,
            normalized_url=listing.source_url, title=title, price=price, publication_status="published",
        )
        snapshot = ListingSnapshot(
            pipeline_run_id=run.id, listing_id=listing.id, product_id=product.id,
            listing_discovery_id=discovery.id, price=price,
        )
        signal = OpportunitySignal(
            id=f"sig-hv-{idx}", pipeline_run_id=run.id, profile_id=profile.id,
            listing_discovery_id=discovery.id, preliminary_score=85.0 if idx == 0 else 72.0,
            score_breakdown={"price_edge": 14.3 if idx == 0 else 5.8},
        )
        db.add_all([listing, discovery, snapshot, signal])

    db.commit()

    service = PipelineChatService(db, gateway=DeterministicLocalModelGateway())
    candidates = service.candidates_for(run.id)
    assert len(candidates) == 2
    assert candidates[0]["title"] == "PS5 urgente"
    assert candidates[0]["price"] == 1500.0
    assert candidates[0]["qualified"] is True
    assert candidates[0]["final_score"] == 85.0

    overview = service._overview(run, service.eligibility_for(run), candidates)
    assert "2 anúncios validados" in overview
    assert "oportunidades com margem real" in overview

    summary = service._opportunity_summary(candidates)
    assert "PS5 urgente" in summary
