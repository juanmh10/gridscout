import datetime
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.core.audit import cleanup_expired_diagnostic_artifacts
from packages.core.database import Base
from packages.core.models import (
    Listing,
    ListingDiscovery,
    ListingEnrichmentTask,
    ListingSnapshot,
    MarketCohort,
    MarketSnapshot,
    PipelineDatasetAnalysis,
    PipelineNavigationLedger,
    PipelineParserArtifact,
    PipelineRetrievalTask,
    PipelineRun,
    PipelineRunScope,
    PipelineRunScopeItem,
    PipelineStateTransition,
    PipelineWorkloadAttempt,
    Product,
    Profile,
)
from packages.marketplace.errors import MarketplaceOperationError
from packages.marketplace.source import FixtureMarketplaceSource, SourceListingDetail
from packages.pipeline import high_volume
from packages.pipeline import runner


def test_time_and_aggressiveness_plan_stays_below_the_olx_navigation_ceiling():
    conservative = high_volume.build_high_volume_plan(duration_minutes=30, aggressiveness="conservative")
    balanced = high_volume.build_high_volume_plan(duration_minutes=30, aggressiveness="balanced")
    intensive = high_volume.build_high_volume_plan(duration_minutes=30, aggressiveness="intensive")

    assert conservative["pace_seconds"] == 120
    assert balanced["pace_seconds"] == 90
    assert intensive["pace_seconds"] == 65
    assert conservative["navigation_cap"] == 15
    assert balanced["navigation_cap"] == 20
    assert intensive["navigation_cap"] == 27
    assert intensive["discovery_navigation_cap"] == 20
    assert intensive["navigation_reserve"] == 7
    assert intensive["max_discovery_pages"] == 20

    three_hours = high_volume.build_high_volume_plan(duration_minutes=180, aggressiveness="intensive")
    assert three_hours["navigation_cap"] == 165
    assert three_hours["navigation_cap"] < 180


@pytest.mark.asyncio
async def test_high_volume_fixture_run_is_paginated_durable_and_finishes(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(autoflush=False, bind=engine)
    db = sessions()
    try:
        profile = Profile(id="high-volume-profile", name="Alto volume", name_normalized="alto volume")
        run = PipelineRun(
            id="high-volume-run",
            profile_id=profile.id,
            status="pending",
            workload_mode="high_volume",
            steps=[{
                "configuration": {
                    "source_type": "fixture",
                    "workload_mode": "high_volume",
                    "scopes": [{"name": "RTX", "query": "RTX", "sort": "recent", "collection_mode": True}],
                    "high_volume": {"discovery_target": 2000, "page_size": 50, "enrichment_target": 90},
                }
            }],
        )
        db.add_all([profile, run])
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(high_volume, "SessionLocal", sessions)
    monkeypatch.setattr(high_volume, "get_marketplace_source", lambda *_: FixtureMarketplaceSource())
    monkeypatch.setattr(runner, "SessionLocal", sessions)
    result = await runner.run_pipeline("high-volume-run", source_type="fixture")

    assert result["status"] == "completed"
    assert result["discovered"] == 1
    assert result["enrichment_completed"] == 1
    assert result["navigations_consumed"] == 3

    check = sessions()
    try:
        run = check.query(PipelineRun).filter(PipelineRun.id == "high-volume-run").one()
        assert run.status == "completed"
        assert run.workload_state["phase"] == "completed"
        assert check.query(PipelineRetrievalTask).filter_by(pipeline_run_id=run.id, status="completed").count() == 2
        assert check.query(ListingDiscovery).filter_by(pipeline_run_id=run.id, triage_status="enriched").count() == 1
        assert check.query(ListingEnrichmentTask).filter_by(pipeline_run_id=run.id, status="completed").count() == 1
        assert check.query(PipelineNavigationLedger).filter_by(pipeline_run_id=run.id, status="completed").count() == 3
        assert check.query(PipelineRunScopeItem).filter_by(match_status="collected").count() == 1
        assert check.query(PipelineWorkloadAttempt).filter_by(pipeline_run_id=run.id).count() >= 1
        assert check.query(PipelineStateTransition).filter_by(pipeline_run_id=run.id).count() >= 1
    finally:
        check.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_finalize_high_volume_run_is_idempotent_and_preserves_pending_details(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(autoflush=False, bind=engine)
    db = sessions()
    try:
        profile = Profile(id="finalize-profile", name="Finalizar", name_normalized="finalizar")
        run = PipelineRun(
            id="finalize-run",
            profile_id=profile.id,
            status="running",
            workload_mode="high_volume",
            steps=[{"configuration": {"workload_mode": "high_volume", "high_volume": {}}}],
        )
        discovery = ListingDiscovery(
            id="finalize-discovery",
            pipeline_run_id=run.id,
            canonical_key="fixture:finalize",
            external_id="finalize-item",
            title="Item preservado",
            triage_status="detail_candidate",
        )
        pending = ListingEnrichmentTask(
            id="finalize-pending",
            pipeline_run_id=run.id,
            listing_discovery_id=discovery.id,
            status="pending",
        )
        failed_discovery = ListingDiscovery(
            id="finalize-failed-discovery",
            pipeline_run_id=run.id,
            canonical_key="fixture:failed",
            external_id="failed-item",
            title="Item com falha",
            triage_status="detail_candidate",
        )
        failed = ListingEnrichmentTask(
            id="finalize-failed",
            pipeline_run_id=run.id,
            listing_discovery_id=failed_discovery.id,
            status="failed",
        )
        db.add_all([profile, run, discovery, pending, failed_discovery, failed])
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(high_volume, "SessionLocal", sessions)
    result = high_volume.finalize_high_volume_run("finalize-run", reason="Encerramento seguro de teste.")

    assert result["status"] == "completed_partial"
    assert result["pending_detail_tasks"] == 1
    check = sessions()
    try:
        run = check.query(PipelineRun).filter_by(id="finalize-run").one()
        assert run.status == "completed_partial"
        assert run.workload_not_before is None
        assert run.workload_state["message"] == "Encerramento seguro de teste."
        assert check.query(ListingEnrichmentTask).filter_by(id="finalize-pending").one().status == "pending"
        assert high_volume.finalize_high_volume_run("finalize-run")["reused"] is True
    finally:
        check.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_task_leases_and_reaper(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(autoflush=False, bind=engine)
    db = sessions()
    try:
        profile = Profile(id="p-lease", name="Lease Test", name_normalized="lease test")
        run = PipelineRun(id="run-lease", profile_id="p-lease", status="running", workload_mode="high_volume", steps=[{}])
        scope = PipelineRunScope(id="scope-lease", pipeline_run_id="run-lease", search_scope_id="ss-1", scope_snapshot={}, status="running")
        disc1 = ListingDiscovery(id="disc-1", pipeline_run_id="run-lease", canonical_key="k1", external_id="ext-1", title="Card 1")
        disc2 = ListingDiscovery(id="disc-2", pipeline_run_id="run-lease", canonical_key="k2", external_id="ext-2", title="Card 2")
        task1 = ListingEnrichmentTask(id="task-1", pipeline_run_id="run-lease", listing_discovery_id="disc-1", status="pending")
        task2 = ListingEnrichmentTask(id="task-2", pipeline_run_id="run-lease", listing_discovery_id="disc-2", status="pending")
        db.add_all([profile, run, scope, disc1, disc2, task1, task2])
        db.commit()

        # Worker 1 claims task1
        claimed1 = high_volume.claim_next_enrichment_task(db, "run-lease", "worker-1", lease_seconds=60)
        assert claimed1 is not None
        assert claimed1.id == "task-1"
        assert claimed1.status == "running"
        assert claimed1.lease_owner == "worker-1"
        assert claimed1.lease_token is not None

        # Worker 2 claims task2
        claimed2 = high_volume.claim_next_enrichment_task(db, "run-lease", "worker-2", lease_seconds=60)
        assert claimed2 is not None
        assert claimed2.id == "task-2"

        # No more pending tasks
        assert high_volume.claim_next_enrichment_task(db, "run-lease", "worker-1") is None

        # Expire task1's lease artificially
        claimed1.lease_expires_at = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(seconds=100)
        db.commit()

        # Reaper runs
        reap_result = high_volume.reap_stale_tasks_and_workloads(db, stale_tolerance_seconds=10)
        assert reap_result["reaped_tasks"] == 1

        # Check task1 is back to pending and can be claimed again
        db.refresh(claimed1)
        assert claimed1.status == "pending"
        assert claimed1.lease_owner is None
        claimed1.not_before = None
        db.commit()

        reclaimed = high_volume.claim_next_enrichment_task(db, "run-lease", "worker-3", lease_seconds=60)
        assert reclaimed is not None
        assert reclaimed.id == "task-1"
        assert reclaimed.lease_owner == "worker-3"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.asyncio
async def test_parser_circuit_and_diagnostic_artifact(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(autoflush=False, bind=engine)
    db = sessions()

    class FlakyParserSource:
        def __init__(self):
            self.fetch_calls = 0

        async def search(self, query):
            from packages.marketplace.source import SourceListingSummary
            return [
                SourceListingSummary(external_id=f"item-{i}", title=f"GPU RTX 3080 item {i}", price=1000.0, url=f"http://test/{i}")
                for i in range(1, 5)
            ]

        async def fetch_listing(self, external_id: str):
            self.fetch_calls += 1
            if external_id == "item-1":
                # Single page missing required fields
                raise MarketplaceOperationError(
                    code="page_missing_required_fields",
                    message="Required fields missing from card",
                    kind="parser",
                    stage="detail",
                    retry_policy="skipped_unverified",
                    http_status=502,
                    missing_fields=["price", "marketplace_item_id"],
                    detail={
                        "diagnostics": {
                            "page_state": "detail_incomplete",
                            "selectors_tried": 5,
                            "provenance": {"json_ld": False},
                            "sanitized_snippet": "<title>Item 1</title>",
                        }
                    },
                )
            return SourceListingDetail(
                external_id=external_id,
                title=f"Valid Item {external_id}",
                description="Valid description",
                price=1200.0,
                location_state="SP",
                location_city="São Paulo",
                seller_name="Seller A",
                condition="used",
                attributes={},
                source_url=f"http://test/{external_id}",
                marketplace_item_id=f"olx-{external_id}",
            )

    try:
        profile = Profile(id="p-circuit", name="Circuit", name_normalized="circuit")
        run = PipelineRun(
            id="run-circuit",
            profile_id="p-circuit",
            status="pending",
            workload_mode="high_volume",
            steps=[{
                "configuration": {
                    "source_type": "fixture",
                    "workload_mode": "high_volume",
                    "high_volume": {"discovery_target": 10, "page_size": 10, "enrichment_target": 5},
                }
            }],
        )
        db.add_all([profile, run])
        db.commit()
    finally:
        db.close()

    source = FlakyParserSource()
    monkeypatch.setattr(high_volume, "SessionLocal", sessions)
    monkeypatch.setattr(high_volume, "get_marketplace_source", lambda *_: source)

    result = await high_volume.run_high_volume_pipeline("run-circuit", source_type="fixture")
    assert result["status"] in {"completed", "completed_partial"}

    check = sessions()
    try:
        # Verify artifact was created for the failed item
        artifact = check.query(PipelineParserArtifact).filter_by(pipeline_run_id="run-circuit").first()
        assert artifact is not None
        assert artifact.http_status == 502
        assert "price" in artifact.missing_fields

        # Verify task 1 was skipped_unverified, while other tasks succeeded
        t1 = check.query(ListingEnrichmentTask).filter_by(pipeline_run_id="run-circuit").first()
        assert t1.status == "skipped_unverified"

        completed_tasks = check.query(ListingEnrichmentTask).filter_by(pipeline_run_id="run-circuit", status="completed").count()
        assert completed_tasks >= 1
    finally:
        check.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_operator_resume_and_attempt_history(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(autoflush=False, bind=engine)
    db = sessions()
    try:
        profile = Profile(id="p-resume", name="Resume", name_normalized="resume")
        run = PipelineRun(
            id="run-resume",
            profile_id="p-resume",
            status="completed_partial",
            workload_mode="high_volume",
            steps=[{"configuration": {"workload_mode": "high_volume", "high_volume": {"deadline_hours": 3}}}],
        )
        attempt1 = PipelineWorkloadAttempt(
            id="att-1",
            pipeline_run_id="run-resume",
            attempt_number=1,
            reason="initial",
            status="completed_partial",
        )
        disc1 = ListingDiscovery(id="d-1", pipeline_run_id="run-resume", canonical_key="k1", external_id="e-1", title="Done 1")
        disc2 = ListingDiscovery(id="d-2", pipeline_run_id="run-resume", canonical_key="k2", external_id="e-2", title="Pending 2")
        disc3 = ListingDiscovery(id="d-3", pipeline_run_id="run-resume", canonical_key="k3", external_id="e-3", title="Failed 3")
        t_done = ListingEnrichmentTask(id="t-1", pipeline_run_id="run-resume", listing_discovery_id="d-1", status="completed")
        t_pending = ListingEnrichmentTask(id="t-2", pipeline_run_id="run-resume", listing_discovery_id="d-2", status="pending", lease_owner="dead-worker")
        t_failed = ListingEnrichmentTask(id="t-3", pipeline_run_id="run-resume", listing_discovery_id="d-3", status="failed")

        db.add_all([profile, run, attempt1, disc1, disc2, disc3, t_done, t_pending, t_failed])
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(high_volume, "SessionLocal", sessions)
    resume_result = high_volume.resume_high_volume_run("run-resume", include_failed=False, actor="operator")

    assert resume_result["status"] == "queued"
    assert resume_result["attempt_number"] == 2

    check = sessions()
    try:
        run = check.query(PipelineRun).filter_by(id="run-resume").one()
        assert run.status == "queued"
        assert run.finished_at is None

        # Verify attempt 2 was created
        att2 = check.query(PipelineWorkloadAttempt).filter_by(pipeline_run_id="run-resume", attempt_number=2).one()
        assert att2.reason == "operator_resume"
        assert att2.status == "running"

        # Verify state transition was recorded
        trans = check.query(PipelineStateTransition).filter_by(pipeline_run_id="run-resume", reason_code="operator_resume").one()
        assert trans.actor == "operator"
        assert trans.from_status == "completed_partial"
        assert trans.to_status == "queued"

        # Verify completed task remained completed, pending was reset, failed remained failed
        assert check.query(ListingEnrichmentTask).filter_by(id="t-1").one().status == "completed"
        t2 = check.query(ListingEnrichmentTask).filter_by(id="t-2").one()
        assert t2.status == "pending"
        assert t2.lease_owner is None
        assert check.query(ListingEnrichmentTask).filter_by(id="t-3").one().status == "failed"
    finally:
        check.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_reanalyze_and_retention_cleanup(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(autoflush=False, bind=engine)
    db = sessions()
    try:
        profile = Profile(id="p-reanalyze", name="Reanalyze", name_normalized="reanalyze")
        run = PipelineRun(
            id="run-reanalyze",
            profile_id="p-reanalyze",
            status="completed_partial",
            workload_mode="high_volume",
            steps=[{"configuration": {"source_type": "fixture", "workload_mode": "high_volume"}}],
        )
        disc = ListingDiscovery(id="d-1", pipeline_run_id="run-reanalyze", canonical_key="k1", external_id="e-1", title="GPU RTX 3080", price=2500.0, location="SP")
        cohort = MarketCohort(id="cohort-1", category="gpu", cohort_key="gpu:rtx3080", display_name="RTX 3080")
        prod = Product(id="prod-1", category="gpu", brand="NVIDIA", family="GeForce", model="RTX 3080", display_name="RTX 3080", market_cohort_id="cohort-1")
        listing = Listing(id="list-1", source="fixture", external_id="e-1", title="GPU RTX 3080 10GB", product_id="prod-1", price=2500.0, category="gpu", status="active")
        snap = ListingSnapshot(
            id=1,
            listing_id="list-1",
            pipeline_run_id="run-reanalyze",
            product_id="prod-1",
            market_cohort_id="cohort-1",
            price=2500.0,
            category="gpu",
            analytics_eligible=True,
            status="active",
        )

        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        old_artifact = PipelineParserArtifact(
            id="art-old",
            pipeline_run_id="run-reanalyze",
            parser_version="v1",
            url_hash="h1",
            created_at=now - datetime.timedelta(days=20),
        )
        recent_artifact = PipelineParserArtifact(
            id="art-new",
            pipeline_run_id="run-reanalyze",
            parser_version="v1",
            url_hash="h2",
            created_at=now - datetime.timedelta(days=2),
        )

        db.add_all([profile, run, disc, cohort, prod, listing, snap, old_artifact, recent_artifact])
        db.commit()

        # Test local reanalyze without navigation
        monkeypatch.setattr(high_volume, "SessionLocal", sessions)
        reanalyze_result = high_volume.reanalyze_high_volume_run("run-reanalyze", db=db)
        assert reanalyze_result["reanalyzed"] is True

        da = db.query(PipelineDatasetAnalysis).filter_by(pipeline_run_id="run-reanalyze").one()
        assert da.status == "completed"
        assert da.result["valid_price_count"] == 1

        # Test artifact retention cleanup (14 days)
        deleted = cleanup_expired_diagnostic_artifacts(db, retention_days=14)
        assert deleted == 1
        assert db.query(PipelineParserArtifact).filter_by(id="art-old").first() is None
        assert db.query(PipelineParserArtifact).filter_by(id="art-new").first() is not None
        # Retention never deletes market evidence. Reanalysis adds its
        # card-derived observation alongside the pre-existing detail snapshot.
        assert db.query(Listing).count() == 1
        assert db.query(ListingSnapshot).count() == 2
        assert db.query(ListingSnapshot).filter_by(evidence_level="detail").count() == 1
        assert db.query(ListingSnapshot).filter_by(evidence_level="card").count() == 1
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_price_first_historical_reconciliation_is_idempotent_and_never_requeues_agent(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(autoflush=False, bind=engine)
    db = sessions()
    try:
        profile = Profile(id="p-price-reconcile", name="Preço", name_normalized="preço")
        run = PipelineRun(
            id="run-price-reconcile", profile_id=profile.id, status="completed",
            workload_mode="high_volume",
            steps=[{"configuration": {"source_type": "fixture", "high_volume": {"card_review_mode": "required"}}}],
        )
        listing = Listing(
            id="listing-price-reconcile", source="fixture", external_id="unpriced",
            title="PS5 sem preço", price=None, status="active", audit_status="unaudited",
        )
        discovery = ListingDiscovery(
            id="discovery-price-reconcile", pipeline_run_id=run.id,
            canonical_key="unpriced", external_id="unpriced", listing_id=listing.id,
            title="PS5 sem preço", price=None, price_origin="missing",
            triage_status="detail_candidate", card_analysis_status="completed",
            card_analysis_attempts=1,
        )
        snapshot = ListingSnapshot(
            listing_id=listing.id, pipeline_run_id=run.id,
            listing_discovery_id=discovery.id, evidence_level="card", status="active",
        )
        db.add_all([profile, run, listing, discovery, snapshot])
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(high_volume, "SessionLocal", sessions)
    first = high_volume.reconcile_historical_runs()
    second = high_volume.reconcile_historical_runs()

    assert first["no_direct_price_found"] == 1
    assert first["no_direct_price_previously_reviewed"] == 1
    assert first["eliminated_before_agent"] == 1
    assert second["failed"] == 0
    check = sessions()
    try:
        discovery = check.query(ListingDiscovery).filter_by(id="discovery-price-reconcile").one()
        listing = check.query(Listing).filter_by(id="listing-price-reconcile").one()
        snapshot = check.query(ListingSnapshot).filter_by(listing_discovery_id=discovery.id).one()
        assert discovery.publication_status == "rejected_no_price"
        assert discovery.card_analysis_attempts == 1
        assert listing.status == "inactive"
        assert snapshot.status == "excluded"
    finally:
        check.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_cancel_pipeline_run_transitions_state_and_cancels_pending_tasks(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    db = sessions()

    try:
        profile = Profile(id="p-cancel", name="Cancel Profile", name_normalized="cancel profile")
        run = PipelineRun(
            id="run-cancel",
            profile_id="p-cancel",
            status="running",
            workload_mode="high_volume",
            started_at=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None),
            workload_state={"phase": "discovery", "discovery_target": 100},
        )
        attempt = PipelineWorkloadAttempt(
            id="att-cancel",
            pipeline_run_id="run-cancel",
            attempt_number=1,
            status="running",
            started_at=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None),
        )
        scope = PipelineRunScope(
            id="scope-cancel",
            pipeline_run_id="run-cancel",
            scope_snapshot={},
        )
        task1 = PipelineRetrievalTask(
            id="ret-1",
            pipeline_run_id="run-cancel",
            pipeline_run_scope_id=scope.id,
            page=1,
            status="pending",
        )
        task2 = ListingEnrichmentTask(
            id="enr-1",
            pipeline_run_id="run-cancel",
            listing_discovery_id="disc-1",
            status="pending",
        )
        db.add_all([profile, run, scope, attempt, task1, task2])
        db.commit()

        monkeypatch.setattr(high_volume, "SessionLocal", sessions)
        result = high_volume.cancel_high_volume_run("run-cancel", actor="operator")

        assert result["status"] == "cancelled"
        db.refresh(run)
        db.refresh(attempt)
        db.refresh(task1)
        db.refresh(task2)

        assert run.status == "cancelled"
        assert run.workload_state["phase"] == "cancelled"
        assert attempt.status == "cancelled"
        assert task1.status == "cancelled"
        assert task2.status == "cancelled"

        # Check transition recorded
        transition = db.query(PipelineStateTransition).filter_by(pipeline_run_id="run-cancel").first()
        assert transition is not None
        assert transition.to_status == "cancelled"
        assert transition.reason_code == "operator_cancel"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
