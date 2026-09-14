import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.ai.model_gateway import AgentCardGateResult, AgentCardTargetProduct
from packages.core.database import Base
from packages.core.models import ListingDiscovery, ListingEnrichmentTask, OpportunitySignal, PipelineRun, Profile
from packages.pipeline import discovery_card_analysis, high_volume


class _CardGateway:
    model_extraction = "test-card-agent"

    async def gate_card(self, title, *, card_context=None):
        assert title == "Notebook Dell Latitude 5420 i5 16GB"
        assert card_context["evidence_scope"] == "search_card_only"
        assert card_context["card_price"] == 2500.0
        assert card_context["card_price_origin"] == "dom"
        assert card_context["card_location"] == "São Paulo"
        return AgentCardGateResult(
            verdict="target_product",
            primary_item_type="console",
            target_product=AgentCardTargetProduct(brand="Dell", model="Latitude 5420", variant=None),
            confidence=0.88,
            reason_code="TARGET_PRODUCT",
            evidence=["Notebook Dell Latitude 5420 i5 16GB"],
            schema_version="agent-card-gate-v1",
        )


class _FailingCardGateway:
    model_extraction = "test-failing-agent"

    async def normalize_listing(self, *args, **kwargs):
        raise RuntimeError("provider down")


@pytest.mark.asyncio
async def test_card_agent_queues_only_cards_without_completed_detail_and_preserves_raw_evidence(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(autoflush=False, bind=engine)
    raw_card = {"title": "Notebook Dell Latitude 5420 i5 16GB <br>", "source": "search-card"}
    db = sessions()
    try:
        profile = Profile(id="cards-profile", name="Cards", name_normalized="cards")
        run = PipelineRun(
            id="cards-run",
            profile_id=profile.id,
            status="completed_partial",
            workload_mode="high_volume",
            steps=[{"configuration": {"high_volume": {"card_review_mode": "required"}}}],
        )
        incomplete = ListingDiscovery(
            id="card-incomplete",
            pipeline_run_id=run.id,
            canonical_key="olx:incomplete",
            external_id="incomplete-item",
            normalized_url="https://example.test/incomplete",
            title=raw_card["title"],
            price=2500.0,
            location="São Paulo",
            raw_summary=raw_card,
        )
        detailed = ListingDiscovery(
            id="card-detailed",
            pipeline_run_id=run.id,
            canonical_key="olx:detailed",
            external_id="detailed-item",
            title="Anúncio com detalhe confirmado",
        )
        completed_detail = ListingEnrichmentTask(
            id="detail-completed",
            pipeline_run_id=run.id,
            listing_discovery_id=detailed.id,
            status="completed",
        )
        db.add_all([profile, run, incomplete, detailed, completed_detail])
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(discovery_card_analysis, "SessionLocal", sessions)
    monkeypatch.setattr(high_volume, "SessionLocal", sessions)
    monkeypatch.setattr(discovery_card_analysis, "get_model_gateway", lambda: _CardGateway())

    queued = discovery_card_analysis.queue_latest_incomplete_cards("cards-profile")
    assert queued == {"run_id": "cards-run", "queued": 1, "total": 1}
    assert discovery_card_analysis.claim_next_card_analysis() == "card-incomplete"

    await discovery_card_analysis.process_card_analysis("card-incomplete")

    check = sessions()
    try:
        processed = check.query(ListingDiscovery).filter_by(id="card-incomplete").one()
        skipped = check.query(ListingDiscovery).filter_by(id="card-detailed").one()
        assert processed.card_analysis_status == "completed"
        assert processed.card_analysis_model_id == "test-card-agent"
        assert processed.card_analysis_result["target_product"]["model"] == "Latitude 5420"
        assert processed.raw_summary == raw_card
        assert skipped.card_analysis_status == "not_requested"
        # A single unaudited card is visible, but cannot claim an economic
        # opportunity before a preliminary cohort has enough priced samples.
        assert check.query(OpportunitySignal).filter_by(listing_discovery_id="card-incomplete").count() == 0
        assert high_volume.schedule_signal_enrichment("cards-run", threshold=96)["queued"] == 0
        scheduled = high_volume.schedule_signal_enrichment("cards-run", threshold=95)
        assert scheduled["queued"] == 0
    finally:
        check.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.asyncio
async def test_required_review_provider_failure_is_withheld_and_resumes_for_finalization(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(autoflush=False, bind=engine)
    db = sessions()
    try:
        profile = Profile(id="required-profile", name="Required", name_normalized="required")
        run = PipelineRun(
            id="required-run", profile_id=profile.id, status="reviewing_cards", workload_mode="high_volume",
            workload_state={"olx_seconds_remaining": 300},
            steps=[{"configuration": {"source_type": "fixture", "high_volume": {"card_review_mode": "required"}}}],
        )
        discovery = ListingDiscovery(
            id="required-card", pipeline_run_id=run.id, canonical_key="required", external_id="required",
            normalized_url="https://example.test/required", title="PlayStation 5 Slim completo",
            price=3200, price_origin="dom", triage_status="detail_candidate", card_analysis_status="pending",
        )
        db.add_all([profile, run, discovery])
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(discovery_card_analysis, "SessionLocal", sessions)
    monkeypatch.setattr(discovery_card_analysis, "get_model_gateway", lambda: _FailingCardGateway())
    assert discovery_card_analysis.claim_next_card_analysis() == "required-card"
    await discovery_card_analysis.process_card_analysis("required-card")

    check = sessions()
    try:
        discovery = check.query(ListingDiscovery).filter_by(id="required-card").one()
        run = check.query(PipelineRun).filter_by(id="required-run").one()
        assert discovery.card_analysis_status == "failed"
        assert discovery.publication_status == "withheld_unverified"
        assert run.status == "queued"
        assert run.workload_deadline is not None
    finally:
        check.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
