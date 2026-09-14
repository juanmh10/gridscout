from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.core.database import Base
from packages.core.models import Listing, ListingDiscovery, ListingSnapshot, PipelineRun, Product, Profile, OpportunitySignal
from packages.market.engine import calculate_market_snapshot_from_snapshots
from packages.pipeline.card_projection import project_discovery, project_run_cards, refresh_preliminary_signals


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine, sessionmaker(autoflush=False, bind=engine)


def test_card_projection_publishes_without_detail_and_groups_console_aliases():
    engine, sessions = _session()
    db = sessions()
    try:
        profile = Profile(id="p-card", name="Cards", name_normalized="cards")
        run = PipelineRun(id="run-card", profile_id=profile.id, status="completed", workload_mode="high_volume")
        rows = [
            ListingDiscovery(
                id=f"card-{index}", pipeline_run_id=run.id, canonical_key=f"card-{index}", external_id=f"card-{index}",
                normalized_url=f"https://www.olx.com.br/item/{index}", title=title, price=price,
                triage_status="detail_candidate", location="São Paulo - SP",
            )
            for index, (title, price) in enumerate([
                ("PS5 Slim com controle", 3200), ("PlayStation 5 usada", 3300),
                ("Ps5 mídia física", 3100), ("PLAYSTATION 5 completa", 3400),
                ("PS5 digital", 3000),
            ], start=1)
        ]
        rejected = ListingDiscovery(
            id="card-rejected", pipeline_run_id=run.id, canonical_key="rejected", external_id="rejected",
            normalized_url="https://www.olx.com.br/rejected", title="Capa para PS5", price=30,
            triage_status="rejected_summary",
        )
        db.add_all([profile, run, *rows, rejected])
        db.commit()

        result = project_run_cards(db, run.id)
        assert result["published"] == 5
        assert result["rejected"] == 1
        assert db.query(Listing).count() == 5
        assert {listing.audit_status for listing in db.query(Listing).all()} == {"unaudited"}
        assert {listing.product.display_name for listing in db.query(Listing).all()} == {"Sony PlayStation 5"}

        product_id = db.query(Listing).first().product_id
        market = calculate_market_snapshot_from_snapshots(
            db, product_id=product_id, category="other", source="olx", evidence_tier="preliminary",
        )
        assert market.evidence_tier == "preliminary"
        assert market.included_count == 5
        assert refresh_preliminary_signals(db, run.id) == 5
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_card_without_direct_price_is_rejected_without_a_listing_projection():
    engine, sessions = _session()
    db = sessions()
    try:
        profile = Profile(id="p-missing", name="Missing", name_normalized="missing")
        run = PipelineRun(id="run-missing", profile_id=profile.id, status="completed", workload_mode="high_volume")
        discovery = ListingDiscovery(
            id="card-missing", pipeline_run_id=run.id, canonical_key="missing", external_id="missing",
            normalized_url="https://www.olx.com.br/missing", title="PlayStation 5 sem preço", price=None,
            triage_status="detail_candidate",
        )
        db.add_all([profile, run, discovery])
        db.commit()
        result = project_run_cards(db, run.id)
        assert result["published"] == 0
        assert discovery.publication_status == "rejected_no_price"
        assert discovery.publication_reason == "NO_DIRECT_PRICE"
        assert db.query(Listing).count() == 0
        assert refresh_preliminary_signals(db, run.id) == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_card_agent_cannot_weaken_rule_match_or_overwrite_audited_listing():
    engine, sessions = _session()
    db = sessions()
    try:
        profile = Profile(id="p-audit", name="Audit", name_normalized="audit")
        run = PipelineRun(id="run-audit", profile_id=profile.id, status="completed", workload_mode="high_volume")
        audited_product = Product(
            id="product-audited", category="other", brand="Sony", family="PlayStation 5",
            model="PlayStation 5", display_name="Sony PlayStation 5",
        )
        audited = Listing(
            id="listing-audited", source="olx", external_id="same-card", product_id=audited_product.id,
            title="PlayStation 5 auditada", price=3500, audit_status="audited", evidence_level="detail",
        )
        discovery = ListingDiscovery(
            id="card-audited", pipeline_run_id=run.id, canonical_key="same-card", external_id="same-card",
            normalized_url="https://www.olx.com.br/same-card", title="PS5 usada", price=2900,
            triage_status="detail_candidate",
        )
        db.add_all([profile, run, audited_product, audited, discovery])
        db.commit()

        project_discovery(db, discovery)
        project_discovery(db, discovery, override={
            "category": "other", "item_form": "unknown", "classification_status": "unverified",
            "confidence": 0.2, "brand": "Generic", "model": "Não identificado",
        })
        db.commit()

        preserved = db.query(Listing).filter(Listing.id == audited.id).one()
        snapshot = db.query(ListingSnapshot).filter(ListingSnapshot.listing_discovery_id == discovery.id).one()
        assert preserved.audit_status == "audited"
        assert preserved.price == 3500
        assert snapshot.product_id == audited_product.id
        assert snapshot.classification_status == "confirmed"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_required_agent_gate_fails_closed_and_disabled_mode_has_no_hidden_queue():
    engine, sessions = _session()
    db = sessions()
    try:
        profile = Profile(id="p-gate", name="Gate", name_normalized="gate")
        run = PipelineRun(id="run-gate", profile_id=profile.id, status="running", workload_mode="high_volume")
        discovery = ListingDiscovery(
            id="card-gate", pipeline_run_id=run.id, canonical_key="gate", external_id="gate",
            normalized_url="https://www.olx.com.br/gate", title="PlayStation 5 Slim completo",
            price=3200, price_origin="dom", triage_status="detail_candidate",
        )
        db.add_all([profile, run, discovery])
        db.commit()

        required = project_run_cards(db, run.id, require_agent=True)
        assert required["published"] == 0
        assert required["withheld"] == 1
        assert db.query(Listing).count() == 0

        discovery.card_analysis_status = "failed"
        failed = project_run_cards(db, run.id, require_agent=True)
        assert failed["published"] == 0
        assert discovery.publication_status == "withheld_unverified"

        disabled = project_run_cards(db, run.id, require_agent=False)
        assert disabled["published"] == 1
        assert disabled["agent_pending"] == 0
        assert discovery.card_analysis_status == "failed"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()

def test_card_projection_uses_catalog_reference_price_when_market_insufficient():
    engine, sessions = _session()
    db = sessions()
    try:
        profile = Profile(id="p-ref", name="Ref", name_normalized="ref")
        run = PipelineRun(id="run-ref", profile_id=profile.id, status="completed", workload_mode="high_volume")
        product = Product(id="prod-ref", category="notebook", brand="Samsung", family="Galaxy Book", model="Galaxy Book", display_name="Test", attributes={"reference_price_brl": 4000.0})
        listing = Listing(id="list-ref", source="olx", external_id="olx-ref", title="Test Reference", price=2500.0, condition="used", location_state="SP", location_city="SP", product_id=product.id, analytics_eligible=True)
        discovery = ListingDiscovery(
            id="disc-ref", pipeline_run_id=run.id, canonical_key="ref", external_id="olx-ref", price=2500.0, title="Test Reference", publication_status="published",
            card_analysis_status="completed",
            card_analysis_result={"classification_status": "confirmed", "confidence": 0.9, "brand": "Samsung", "model": "Galaxy Book", "category": "notebook", "attributes": {"category": "notebook", "brand": "Samsung"}},
            listing_id=listing.id
        )
        db.add_all([profile, run, product, listing, discovery])
        db.commit()

        # baseline should be 4000 * 0.65 = 2600
        # price = 2500
        # edge = (2600 - 2500) / 2600 = 0.0384
        # score = 35 + 0 + 15 + edge * 25 = 50 + 0.96 = 50.96 => qualifies = score >= 60 or edge >= 0.12 => false?
        # Wait, if we want it to qualify, we should use a lower price.
        discovery.price = 2200.0
        # baseline = 2600, price = 2200
        # edge = (2600 - 2200) / 2600 = 400 / 2600 = 0.1538 -> >= 0.12 (qualifies)
        db.commit()

        created = refresh_preliminary_signals(db, run.id)
        assert created == 1
        signal = db.query(OpportunitySignal).filter(OpportunitySignal.pipeline_run_id == run.id).first()
        assert signal is not None
        assert signal.stage == "preliminary"
        assert abs(signal.score_breakdown["price_edge"] - (0.1538 * 25.0)) < 0.1
    finally:
        db.rollback()
        db.close()
