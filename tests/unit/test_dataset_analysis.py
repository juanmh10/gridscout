import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.ai.model_gateway import PipelineChatResult
from packages.core.database import Base
from packages.core.models import (
    ListingDiscovery,
    ListingEnrichmentTask,
    PipelineDatasetAnalysis,
    PipelineRetrievalTask,
    PipelineRun,
    Profile,
)
from packages.pipeline import dataset_analysis


class _AggregateGateway:
    model_chat = "test-aggregate-agent"

    async def analyze_pipeline_chat(self, prompt, *, allow_web_search=False, operation="chat"):
        assert allow_web_search is False
        assert operation == "dataset_analysis"
        assert "search_card_only" in prompt
        return PipelineChatResult(content="Síntese do agente baseada somente nos agregados.")


@pytest.mark.asyncio
async def test_dataset_analysis_preserves_raw_cards_and_records_aggregate_reading(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(autoflush=False, bind=engine)
    db = sessions()
    raw_card = {
        "title": "Notebook Dell Latitude 5420 i5 16GB RAM SSD 512GB <br> R$ 2.500,00",
        "url": "https://sp.olx.com.br/sao-paulo-e-regiao/informatica/notebooks/notebook-dell-1",
    }
    try:
        profile = Profile(id="dataset-profile", name="Dataset", name_normalized="dataset")
        run = PipelineRun(
            id="dataset-run",
            profile_id=profile.id,
            workload_mode="high_volume",
            steps=[{"configuration": {"source_type": "olx", "workload_mode": "high_volume"}}],
        )
        discovery = ListingDiscovery(
            id="discovery-1",
            pipeline_run_id=run.id,
            canonical_key="olx:1",
            external_id="https://sp.olx.com.br/sao-paulo-e-regiao/informatica/notebooks/notebook-dell-1",
            normalized_url=raw_card["url"],
            title=raw_card["title"],
            raw_summary=raw_card,
            triage_status="detail_candidate",
        )
        retrieval = PipelineRetrievalTask(
            id="retrieval-1",
            pipeline_run_id=run.id,
            pipeline_run_scope_id="missing-scope-is-not-checked-by-sqlite",
            page=1,
            status="completed",
        )
        enrichment = ListingEnrichmentTask(
            id="enrichment-1",
            pipeline_run_id=run.id,
            listing_discovery_id=discovery.id,
            status="failed",
            error_code="olx_listing_extraction_failed",
        )
        db.add_all([profile, run, discovery, retrieval, enrichment])
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(dataset_analysis, "SessionLocal", sessions)
    result = await dataset_analysis.analyze_pipeline_dataset("dataset-run", gateway=_AggregateGateway())

    assert result == {
        "status": "completed",
        "agent_status": "completed",
        "discoveries": 1,
        "reused": False,
    }
    check = sessions()
    try:
        persisted = check.query(PipelineDatasetAnalysis).filter_by(pipeline_run_id="dataset-run").one()
        assert persisted.result["capture_quality"]["price_card"]["count"] == 0
        assert persisted.result["capture_quality"]["price_inferred_from_title"]["count"] == 1
        assert persisted.result["derived_market"]["explicit_or_title_price"]["median"] == 2500.0
        assert persisted.result["derived_market"]["brands"][0]["label"] == "Dell"
        assert persisted.result["backend_readiness"]["recovery"]["failure_types"][0]["label"] == "olx_listing_extraction_failed"
        assert persisted.agent_summary.startswith("Síntese")
        assert check.query(ListingDiscovery).filter_by(id="discovery-1").one().raw_summary == raw_card
    finally:
        check.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
