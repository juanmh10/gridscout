import math

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.core.database import Base
from packages.core.models import Listing, ListingDiscovery, PipelineRun, PipelineRunScope, Profile
from packages.pipeline.card_gate import apply_cheap_gate
from packages.pipeline.card_projection import project_run_cards
from packages.pipeline.discovery_card_analysis import _queue_eligible_cards


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine, sessionmaker(autoflush=False, bind=engine)


def _discovery(run_id: str, card_id: str, title: str, price, *, origin="dom", raw=""):
    return ListingDiscovery(
        id=card_id,
        pipeline_run_id=run_id,
        canonical_key=card_id,
        external_id=card_id,
        normalized_url=f"https://example.test/{card_id}",
        title=title,
        price=price,
        price_origin=origin,
        price_raw=raw,
        triage_status="detail_candidate",
    )


@pytest.mark.parametrize(
    ("price", "origin", "raw", "reason"),
    [
        (None, "missing", "", "NO_DIRECT_PRICE"),
        (0, "dom", "R$ 0", "INVALID_DIRECT_PRICE"),
        (-1, "dom", "R$ -1", "INVALID_DIRECT_PRICE"),
        (math.inf, "dom", "R$ infinito", "INVALID_DIRECT_PRICE"),
        (3200, "title", "", "TITLE_PRICE_ONLY"),
        (None, "dom", "a combinar", "NO_DIRECT_PRICE"),
    ],
)
def test_invalid_or_inferred_price_never_enters_agent_queue(price, origin, raw, reason):
    engine, sessions = _session()
    db = sessions()
    try:
        profile = Profile(id="price-profile", name="Preço", name_normalized="preço")
        run = PipelineRun(id="price-run", profile_id=profile.id, workload_mode="high_volume")
        discovery = _discovery(run.id, f"card-{reason}", "PS5 Slim R$ 3200", price, origin=origin, raw=raw)
        db.add_all([profile, run, discovery])
        db.commit()

        decision = apply_cheap_gate(discovery)
        assert decision.reason_code == reason
        assert _queue_eligible_cards([discovery]) == 0
        assert discovery.card_analysis_status == "not_needed"
        result = project_run_cards(db, run.id)
        assert result["published"] == 0
        assert discovery.publication_status == "rejected_no_price"
        assert db.query(Listing).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.parametrize(
    ("title", "reason"),
    [
        ("Controle para PS5 original", "ACCESSORY_ONLY"),
        ("Suporte de parede para PS5", "ACCESSORY_ONLY"),
        ("Jogo PS5 Armored Core", "GAME_ONLY"),
        ("Mídia física Spider-Man PS5", "GAME_ONLY"),
        ("Conserto de PS4 e PS5", "SERVICE_ONLY"),
        ("Assistência técnica especializada", "SERVICE_ONLY"),
        ("Compro PS5", "WANTED_BUYER_AD"),
        ("Procuro PS5 pago no pix", "WANTED_BUYER_AD"),
        ("Caixa PS5 Slim vazia", "BOX_ONLY"),
        ("Embalagem vazia PS5", "BOX_ONLY"),
        ("Peça leitor PS5 para peças", "PARTS_ONLY"),
        ("PS5 com defeito na placa não liga", "PARTS_ONLY"),
    ],
)
def test_clear_non_console_cards_are_rejected_before_agent(title, reason):
    engine, sessions = _session()
    db = sessions()
    try:
        profile = Profile(id="prefilter-profile", name="Filtro", name_normalized="filtro")
        run = PipelineRun(id="prefilter-run", profile_id=profile.id, workload_mode="high_volume")
        discovery = _discovery(run.id, f"card-{reason}", title, 200, raw="R$ 200")
        db.add_all([profile, run, discovery])
        db.commit()

        decision = apply_cheap_gate(discovery)
        assert decision.reason_code == reason
        assert _queue_eligible_cards([discovery]) == 0
        project_run_cards(db, run.id)
        assert discovery.publication_status == "rejected_prefilter"
        assert db.query(Listing).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.parametrize("title", ["PS5 Slim com dois jogos", "PS5 com controle original"])
def test_console_bundles_remain_eligible_for_deterministic_projection(title):
    engine, sessions = _session()
    db = sessions()
    try:
        profile = Profile(id="bundle-profile", name="Bundle", name_normalized="bundle")
        run = PipelineRun(id="bundle-run", profile_id=profile.id, workload_mode="high_volume")
        discovery = _discovery(run.id, f"card-{title[:8]}", title, 3200, raw="R$ 3.200")
        db.add_all([profile, run, discovery])
        db.commit()

        assert apply_cheap_gate(discovery).approved
        result = project_run_cards(db, run.id)
        assert result["published"] == 1
        assert discovery.publication_status == "published"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_price_above_scope_and_invalid_agent_decisions_fail_closed():
    engine, sessions = _session()
    db = sessions()
    try:
        profile = Profile(id="required-profile", name="Obrigatório", name_normalized="obrigatório")
        run = PipelineRun(id="required-run", profile_id=profile.id, workload_mode="high_volume")
        scope = PipelineRunScope(
            id="required-scope", pipeline_run_id=run.id,
            scope_snapshot={"max_price": 3800},
        )
        expensive = _discovery(run.id, "expensive", "PS5 Slim", 3801, raw="R$ 3.801")
        expensive.scope_run = scope
        invalid_agent = _discovery(run.id, "invalid-agent", "PS5 Slim", 3200, raw="R$ 3.200")
        invalid_agent.scope_run = scope
        invalid_agent.card_analysis_status = "completed"
        invalid_agent.card_analysis_result = {"verdict": "target_product", "confidence": 0.99}
        low_confidence = _discovery(run.id, "low-confidence", "PS5 Slim", 3200, raw="R$ 3.200")
        low_confidence.scope_run = scope
        low_confidence.card_analysis_status = "completed"
        low_confidence.card_analysis_result = {
            "verdict": "target_product", "primary_item_type": "console",
            "target_product": {"brand": "Sony", "model": "PlayStation 5", "variant": "Slim"},
            "confidence": 0.74, "reason_code": "LOW", "evidence": ["PS5 Slim"],
            "schema_version": "agent-card-gate-v1",
        }
        db.add_all([profile, run, scope, expensive, invalid_agent, low_confidence])
        db.commit()

        result = project_run_cards(db, run.id, require_agent=True)
        assert result["published"] == 0
        assert expensive.publication_status == "rejected_scope"
        assert invalid_agent.publication_status == "withheld_unverified"
        assert invalid_agent.publication_reason == "AGENT_INVALID_RESPONSE"
        assert low_confidence.publication_status == "withheld_unverified"
        assert low_confidence.publication_reason == "AGENT_LOW_CONFIDENCE"
        assert db.query(Listing).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
