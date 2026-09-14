"""Durable, paced high-volume listing extraction with atomic leases, typed errors, and decoupled analytics.

The normal pipeline deliberately remains small and immediate. This module is
the separate execution path for a saved search that needs broad, resumable
discovery without turning transient marketplace capacity or layout changes
into lost discovery work.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, desc, func, or_

from packages.ai.metrics import model_metric_context
from packages.ai.model_gateway import DeterministicLocalModelGateway, get_model_gateway
from packages.core.database import SessionLocal
from packages.core.models import (
    Listing,
    ListingAnalysis,
    ListingDiscovery,
    ListingEnrichmentTask,
    ListingSnapshot,
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
    OpportunitySignal,
    Opportunity,
    OpportunityEvaluation,
    MarketSnapshot,
    ModelMetricCall,
)
from packages.marketplace.errors import MarketplaceOperationError, OlxAccessError
from packages.marketplace.source import SearchQuery, get_marketplace_source
from packages.search.matching import evaluate_candidate
from packages.search.models import SearchCandidate, SearchPlanV1

logger = logging.getLogger(__name__)

DEFAULTS = {
    "discovery_target": 2000,
    "page_size": 50,
    "max_discovery_pages": 70,
    "enrichment_target": 90,
    "enrichment_cap": 100,
    "navigation_cap": 165,
    "navigation_reserve": 15,
    "pace_seconds": 65,
    "deadline_hours": 3,
    "parser_consecutive_failure_limit": 3,
    "parser_failure_rate_window": 20,
    "parser_failure_rate_limit": 0.10,
    "lease_seconds": 120,
    "heartbeat_interval_seconds": 20,
    "enrichment_threshold": 70,
}

AGGRESSIVENESS_PACE_SECONDS = {
    "conservative": 120,
    "balanced": 90,
    "intensive": 65,
}
MAX_DURATION_MINUTES = 180
MIN_DURATION_MINUTES = 5


def build_high_volume_plan(
    *,
    duration_minutes: int = MAX_DURATION_MINUTES,
    aggressiveness: str = "balanced",
    discovery_target: int = 2000,
    detail_access_mode: str = "disabled",
    enrichment_threshold: int = 70,
) -> dict[str, int | str]:
    """Build the immutable, safe workload budget shared by forecast and run.

    The OLX browser guard remains authoritative.  This plan intentionally stays
    below its hourly ceiling through the selected cadence and caps a single
    workload at the established 165-navigation maximum.
    """
    if duration_minutes < MIN_DURATION_MINUTES or duration_minutes > MAX_DURATION_MINUTES:
        raise ValueError(f"duration_minutes must be between {MIN_DURATION_MINUTES} and {MAX_DURATION_MINUTES}")
    if aggressiveness not in AGGRESSIVENESS_PACE_SECONDS:
        raise ValueError("aggressiveness must be conservative, balanced, or intensive")

    pace_seconds = AGGRESSIVENESS_PACE_SECONDS[aggressiveness]
    navigation_cap = min(165, max(1, (duration_minutes * 60) // pace_seconds))
    discovery_navigation_cap = max(1, (navigation_cap * 75) // 100)
    navigation_reserve = max(0, navigation_cap - discovery_navigation_cap)
    page_size = 50
    discovery_target = max(1, min(int(discovery_target), 2000))
    retrieval_pages = min(
        70,
        discovery_navigation_cap,
        max(1, math.ceil(discovery_target / page_size)),
    )

    return {
        "duration_minutes": duration_minutes,
        "aggressiveness": aggressiveness,
        "pace_seconds": pace_seconds,
        "navigation_cap": navigation_cap,
        "discovery_navigation_cap": discovery_navigation_cap,
        "navigation_reserve": navigation_reserve,
        "page_size": page_size,
        "max_discovery_pages": retrieval_pages,
        "discovery_target": discovery_target,
        # Details may consume unused discovery capacity, but never more than
        # the existing safety cap of one hundred detail pages per workload.
        "enrichment_target": min(100, max(1, navigation_reserve)),
        "enrichment_cap": min(100, navigation_cap),
        "deadline_hours": max(1, math.ceil(duration_minutes / 60)),
        "detail_access_mode": detail_access_mode if detail_access_mode in {"disabled", "auto_threshold"} else "disabled",
        "enrichment_threshold": max(0, min(int(enrichment_threshold), 100)),
    }


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _configuration(run: PipelineRun) -> dict[str, Any]:
    return ((run.steps or [{}])[0]).get("configuration", {})


def _settings(configuration: dict[str, Any]) -> dict[str, Any]:
    supplied = configuration.get("high_volume") or {}
    values: dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        try:
            if isinstance(default, float):
                values[key] = float(supplied.get(key, default))
            else:
                values[key] = int(supplied.get(key, default))
        except (TypeError, ValueError):
            values[key] = default
    values["discovery_target"] = max(1, min(values["discovery_target"], 2000))
    values["page_size"] = max(1, min(values["page_size"], 100))
    values["max_discovery_pages"] = max(1, min(values["max_discovery_pages"], 70))
    values["enrichment_target"] = max(1, min(values["enrichment_target"], 100))
    values["enrichment_cap"] = max(values["enrichment_target"], min(values["enrichment_cap"], 100))
    values["navigation_cap"] = max(1, min(values["navigation_cap"], 165))
    values["navigation_reserve"] = max(0, min(values["navigation_reserve"], values["navigation_cap"]))
    values["pace_seconds"] = max(5, values["pace_seconds"])
    values["deadline_hours"] = max(1, values["deadline_hours"])
    values["parser_consecutive_failure_limit"] = max(1, values["parser_consecutive_failure_limit"])
    values["parser_failure_rate_window"] = max(5, values["parser_failure_rate_window"])
    values["parser_failure_rate_limit"] = max(0.01, min(values["parser_failure_rate_limit"], 1.0))
    values["lease_seconds"] = max(30, values["lease_seconds"])
    values["heartbeat_interval_seconds"] = max(5, values["heartbeat_interval_seconds"])
    try:
        supplied_duration = int(supplied.get("duration_minutes", MAX_DURATION_MINUTES))
    except (TypeError, ValueError):
        supplied_duration = MAX_DURATION_MINUTES
    values["duration_minutes"] = max(MIN_DURATION_MINUTES, min(MAX_DURATION_MINUTES, supplied_duration))
    aggressiveness = str(supplied.get("aggressiveness", "balanced"))
    values["aggressiveness"] = aggressiveness if aggressiveness in AGGRESSIVENESS_PACE_SECONDS else "balanced"
    # Existing snapshots predate the toggle and retain their automatic-detail
    # behavior; every new API-created run persists the explicit disabled default.
    detail_access_mode = str(supplied.get("detail_access_mode", "auto_threshold"))
    values["detail_access_mode"] = detail_access_mode if detail_access_mode in {"disabled", "auto_threshold"} else "disabled"
    values["signal_threshold_enabled"] = "detail_access_mode" in supplied
    card_review_mode = str(supplied.get("card_review_mode", "disabled"))
    values["card_review_mode"] = card_review_mode if card_review_mode in {"disabled", "required"} else "disabled"
    values["time_controlled"] = "duration_minutes" in supplied or "aggressiveness" in supplied
    try:
        supplied_discovery_cap = int(supplied.get("discovery_navigation_cap", values["max_discovery_pages"]))
    except (TypeError, ValueError):
        supplied_discovery_cap = values["max_discovery_pages"]
    values["discovery_navigation_cap"] = max(
        1,
        min(
            values["navigation_cap"],
            supplied_discovery_cap,
        ),
    )
    return values


def _is_live_source(source_type: str) -> bool:
    return source_type == "olx"


def _run_scopes(configuration: dict[str, Any], source_type: str, query: str, limit: int) -> list[dict[str, Any]]:
    scopes = configuration.get("scopes") or []
    if scopes:
        return [dict(scope) for scope in scopes]
    return [{
        "id": None,
        "name": "Busca preparada",
        "marketplace": source_type,
        "query": query,
        "category": None,
        "min_price": None,
        "max_price": None,
        "sort": "recent",
        "limit": max(1, min(limit, 100)),
    }]


def _record_state_transition(
    db,
    run: PipelineRun,
    to_status: str,
    reason_code: str,
    *,
    attempt_number: int = 1,
    actor: str = "scheduler",
    message: str | None = None,
) -> None:
    from_status = run.status
    run.status = to_status
    db.add(PipelineStateTransition(
        pipeline_run_id=run.id,
        from_status=from_status,
        to_status=to_status,
        reason_code=reason_code,
        occurred_at=_now(),
        attempt_number=attempt_number,
        actor=actor,
        message=message,
    ))


def _get_or_create_active_attempt(
    db,
    run: PipelineRun,
    reason: str = "initial",
) -> PipelineWorkloadAttempt:
    existing = (
        db.query(PipelineWorkloadAttempt)
        .filter(
            PipelineWorkloadAttempt.pipeline_run_id == run.id,
            PipelineWorkloadAttempt.status.in_(("running", "queued", "waiting_budget", "waiting_worker")),
        )
        .order_by(PipelineWorkloadAttempt.attempt_number.desc())
        .first()
    )
    if existing:
        return existing

    last = (
        db.query(PipelineWorkloadAttempt)
        .filter(PipelineWorkloadAttempt.pipeline_run_id == run.id)
        .order_by(PipelineWorkloadAttempt.attempt_number.desc())
        .first()
    )
    attempt_number = (last.attempt_number + 1) if last else 1
    settings = _settings(_configuration(run))
    now = _now()
    attempt = PipelineWorkloadAttempt(
        id=f"attempt-{uuid.uuid4().hex[:20]}",
        pipeline_run_id=run.id,
        attempt_number=attempt_number,
        reason=reason,
        status="running",
        started_at=now,
        deadline_at=run.workload_deadline or (now + timedelta(hours=settings["deadline_hours"])),
        worker_revision=os.getenv("BUILD_SHA", "dev"),
        parser_version="olx-dom-2026-08-24",
    )
    db.add(attempt)
    db.flush()
    return attempt


def _finalize_attempt(
    db,
    run: PipelineRun,
    status: str,
    *,
    error_code: str | None = None,
    summary: str | None = None,
) -> None:
    attempt = (
        db.query(PipelineWorkloadAttempt)
        .filter(
            PipelineWorkloadAttempt.pipeline_run_id == run.id,
            PipelineWorkloadAttempt.status.in_(("running", "queued", "waiting_budget", "waiting_worker")),
        )
        .order_by(PipelineWorkloadAttempt.attempt_number.desc())
        .first()
    )
    if attempt:
        attempt.status = status
        attempt.finished_at = _now()
        attempt.error_code = error_code
        attempt.summary = summary


def _state(run: PipelineRun, settings: dict[str, Any], phase: str) -> dict[str, Any]:
    current = dict(run.workload_state or {})
    current.update({
        "mode": "high_volume",
        "phase": phase,
        "discovery_target": settings["discovery_target"],
        "enrichment_target": settings["enrichment_target"],
        "enrichment_cap": settings["enrichment_cap"],
        "navigation_cap": settings["navigation_cap"],
        "discovery_navigation_cap": settings["discovery_navigation_cap"],
        "navigation_reserve": settings["navigation_reserve"],
        "pace_seconds": settings["pace_seconds"],
        "detail_access_mode": settings.get("detail_access_mode", "disabled"),
        "card_review_mode": settings.get("card_review_mode", "disabled"),
        "enrichment_threshold": settings.get("enrichment_threshold", 70),
        "deadline_at": run.workload_deadline.isoformat() if run.workload_deadline else None,
        "not_before": run.workload_not_before.isoformat() if run.workload_not_before else None,
    })
    if settings["time_controlled"]:
        current["duration_minutes"] = settings["duration_minutes"]
        current["aggressiveness"] = settings["aggressiveness"]
    return current


def _refresh_counts(db, run: PipelineRun, settings: dict[str, Any], phase: str) -> dict[str, Any]:
    discoveries = db.query(ListingDiscovery).filter(ListingDiscovery.pipeline_run_id == run.id)
    retrievals = db.query(PipelineRetrievalTask).filter(PipelineRetrievalTask.pipeline_run_id == run.id)
    enrichments = db.query(ListingEnrichmentTask).filter(ListingEnrichmentTask.pipeline_run_id == run.id)
    signals = db.query(OpportunitySignal).filter(OpportunitySignal.pipeline_run_id == run.id)
    agent_cost = db.query(func.coalesce(func.sum(ModelMetricCall.estimated_cost_usd), 0.0)).filter(
        ModelMetricCall.pipeline_run_id == run.id,
        ModelMetricCall.origin == "discovery_card_analysis",
    ).scalar()
    state = _state(run, settings, phase)
    rejected_statuses = ("rejected", "rejected_no_price", "rejected_scope", "rejected_prefilter", "agent_rejected")
    state.update({
        "observed": discoveries.count(),
        "discovered": discoveries.filter(ListingDiscovery.triage_status != "duplicate").count(),
        "cards_published": discoveries.filter(ListingDiscovery.publication_status == "published").count(),
        "cards_rejected": discoveries.filter(ListingDiscovery.publication_status.in_(rejected_statuses)).count(),
        "cards_withheld": discoveries.filter(ListingDiscovery.publication_status == "withheld_unverified").count(),
        "cards_approved": discoveries.filter(ListingDiscovery.publication_status == "approved").count(),
        "rejected_no_price": discoveries.filter(ListingDiscovery.publication_status == "rejected_no_price").count(),
        "rejected_scope": discoveries.filter(ListingDiscovery.publication_status == "rejected_scope").count(),
        "rejected_prefilter": discoveries.filter(ListingDiscovery.publication_status == "rejected_prefilter").count(),
        "agent_eligible": discoveries.filter(ListingDiscovery.gate_status == "agent_eligible").count(),
        "agent_approved": discoveries.filter(ListingDiscovery.publication_status == "agent_approved").count(),
        "agent_rejected": discoveries.filter(ListingDiscovery.publication_status == "agent_rejected").count(),
        "agent_pending": discoveries.filter(ListingDiscovery.card_analysis_status.in_(("pending", "running"))).count(),
        "agent_completed": discoveries.filter(ListingDiscovery.card_analysis_status == "completed").count(),
        "agent_failed": discoveries.filter(ListingDiscovery.card_analysis_status == "failed").count(),
        "agent_estimated_cost_usd": round(float(agent_cost or 0.0), 6),
        "detail_candidates": discoveries.filter(ListingDiscovery.triage_status == "detail_candidate").count(),
        "retrieval_pending": retrievals.filter(PipelineRetrievalTask.status == "pending").count(),
        "retrieval_completed": retrievals.filter(PipelineRetrievalTask.status == "completed").count(),
        "enrichment_planned": enrichments.count(),
        "enrichment_pending": enrichments.filter(ListingEnrichmentTask.status == "pending").count(),
        "enrichment_completed": enrichments.filter(ListingEnrichmentTask.status == "completed").count(),
        "enrichment_failed": enrichments.filter(ListingEnrichmentTask.status.in_(("failed", "skipped_unverified"))).count(),
        "preliminary_opportunities": signals.filter(OpportunitySignal.stage.in_(("preliminary", "queued_for_enrichment"))).count(),
        "confirmed_opportunities": signals.filter(OpportunitySignal.stage == "confirmed").count(),
        "withdrawn_opportunities": signals.filter(OpportunitySignal.stage == "withdrawn").count(),
        "navigations_consumed": db.query(PipelineNavigationLedger).filter(
            PipelineNavigationLedger.pipeline_run_id == run.id,
            PipelineNavigationLedger.status == "completed",
        ).count(),
    })
    no_direct = discoveries.filter(
        or_(
            ListingDiscovery.price.is_(None),
            ListingDiscovery.price <= 0,
            ListingDiscovery.price_origin.notin_(("structured", "dom")),
        )
    )
    state["agent_calls_for_no_price"] = no_direct.filter(ListingDiscovery.card_analysis_attempts > 0).count()
    state["details_for_no_price"] = db.query(ListingEnrichmentTask).join(
        ListingDiscovery, ListingEnrichmentTask.listing_discovery_id == ListingDiscovery.id,
    ).filter(
        ListingEnrichmentTask.pipeline_run_id == run.id,
        or_(
            ListingDiscovery.price.is_(None),
            ListingDiscovery.price <= 0,
            ListingDiscovery.price_origin.notin_(("structured", "dom")),
        ),
    ).count()
    state["published_without_direct_price"] = no_direct.filter(
        ListingDiscovery.publication_status == "published"
    ).count()
    agent_rows = discoveries.filter(ListingDiscovery.card_analysis_status == "completed").all()
    def approved_agent_verdict(row: ListingDiscovery, verdict: str) -> bool:
        result = row.card_analysis_result or {}
        try:
            confidence = float(result.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        return result.get("verdict") == verdict and confidence >= 0.75
    state["agent_approved"] = sum(
        approved_agent_verdict(row, "target_product")
        for row in agent_rows
    )
    state["agent_rejected"] = sum(
        approved_agent_verdict(row, "exclude")
        for row in agent_rows
    )
    run.workload_state = state
    terminal = discoveries.filter(
        ListingDiscovery.publication_status.in_((
            "published", "rejected", "rejected_no_price", "rejected_scope", "rejected_prefilter",
            "agent_rejected", "withheld_unverified",
        ))
    ).count()
    snapshots = db.query(ListingSnapshot).filter(ListingSnapshot.pipeline_run_id == run.id)
    run.processed_count = int(terminal)
    run.snapshots_created = int(snapshots.count())
    run.products_normalized = int(snapshots.filter(ListingSnapshot.product_id.is_not(None)).with_entities(ListingSnapshot.product_id).distinct().count())
    run.opportunities_found = int(state["confirmed_opportunities"])
    return state


def _set_step(run: PipelineRun, status: str, state: dict[str, Any]) -> None:
    steps = list(run.steps or [])
    high_volume_step = {
        "name": "high_volume_extraction",
        "status": status,
        "items_in": state.get("observed", 0),
        "items_out": state.get("enrichment_completed", 0),
        "details": state,
    }
    index = next((i for i, step in enumerate(steps) if step.get("name") == "high_volume_extraction"), None)
    if index is None:
        steps.append(high_volume_step)
    else:
        steps[index] = high_volume_step
    run.steps = steps


def _run_dataset_analysis(db, run: PipelineRun, source_type: str) -> None:
    del source_type
    from packages.pipeline.dataset_analysis import persist_dataset_report

    persist_dataset_report(db, run.id)


def _run_market_consolidation(db, run: PipelineRun, source_type: str) -> None:
    from packages.market.engine import calculate_market_snapshot_from_snapshots
    from packages.core.hardware_taxonomy import supports_market_analysis

    observed_product_ids = [
        row[0]
        for row in db.query(Listing.product_id)
        .join(ListingSnapshot, ListingSnapshot.listing_id == Listing.id)
        .filter(ListingSnapshot.pipeline_run_id == run.id, Listing.product_id.is_not(None))
        .distinct()
        .all()
    ]
    for pid in observed_product_ids:
        prod = db.query(Product).filter(Product.id == pid).first()
        if prod and supports_market_analysis(prod.category):
            calculate_market_snapshot_from_snapshots(
                db,
                product_id=prod.id,
                market_cohort_id=prod.market_cohort_id,
                category=prod.category,
                source=source_type,
                pipeline_run_id=run.id,
                window_days=30,
                evidence_tier="audited",
            )
            calculate_market_snapshot_from_snapshots(
                db,
                product_id=prod.id,
                market_cohort_id=prod.market_cohort_id,
                category=prod.category,
                source=source_type,
                pipeline_run_id=run.id,
                window_days=30,
                evidence_tier="preliminary",
            )


def _reconcile_opportunity_signals(db, run: PipelineRun, source_type: str) -> None:
    """Promote or withdraw card signals after the full evidence flow."""
    from packages.market.engine import compute_opportunity_score
    from packages.pipeline.runner import _default_preferences

    preferences = _default_preferences(db, run.profile_id)
    signals = db.query(OpportunitySignal).filter(
        OpportunitySignal.pipeline_run_id == run.id,
        OpportunitySignal.stage.in_(("queued_for_enrichment", "enriched_pending_scoring")),
    ).all()
    confirmed = 0
    for signal in signals:
        task = db.query(ListingEnrichmentTask).filter(
            ListingEnrichmentTask.listing_discovery_id == signal.listing_discovery_id,
        ).first()
        if not task or task.status != "completed" or not task.listing_id:
            continue
        listing = db.query(Listing).filter(Listing.id == task.listing_id).first()
        product = db.query(Product).filter(Product.id == listing.product_id).first() if listing and listing.product_id else None
        snapshot = db.query(ListingSnapshot).filter(
            ListingSnapshot.listing_id == task.listing_id,
            ListingSnapshot.pipeline_run_id == run.id,
        ).order_by(ListingSnapshot.observed_at.desc()).first()
        market = db.query(MarketSnapshot).filter(
            MarketSnapshot.product_id == listing.product_id,
            MarketSnapshot.source == source_type,
        ).order_by(MarketSnapshot.calculated_at.desc()).first() if listing else None
        signal.updated_at = _now()
        
        stats = None
        market_snapshot_id = None
        if market and market.included_count >= 5:
            stats = {
                "estimated_clearing_value": market.estimated_clearing_value,
                "fast_sale_value": market.fast_sale_value,
                "market_heat": market.market_heat,
                "heat_band": market.heat_band,
                "confidence": market.confidence,
            }
            market_snapshot_id = market.id
        elif product and product.attributes.get("reference_price_brl"):
            used_baseline = float(product.attributes["reference_price_brl"]) * 0.65
            stats = {
                "estimated_clearing_value": used_baseline,
                "fast_sale_value": used_baseline * 0.9,
                "market_heat": 50.0,
                "heat_band": "warm",
                "confidence": 0.5,
            }
            
        if not listing or not product or not stats or not listing.analytics_eligible:
            signal.stage = "withdrawn"
            signal.full_flow_completed = False
            signal.eligibility_reasons = [*(signal.eligibility_reasons or []), "full_evidence_not_eligible"]
            continue
        freshness = max(0.0, (_now() - listing.first_seen).total_seconds() / 86400.0)
        score, breakdown, edge, explanation = compute_opportunity_score(
            listing_price=listing.price, market_stats=stats, preferences=preferences,
            condition=listing.condition, freshness_days=freshness,
            is_preferred_location=bool(preferences.get("preferred_location")) and listing.location_state == preferences.get("preferred_location"),
            risk_factors=["whatsapp_scam"] if "WHATS" in listing.title.upper() else [],
        )
        qualifies = score >= 60.0 or edge >= 0.12
        opportunity_id = f"opp-{hashlib.sha1(listing.id.encode()).hexdigest()[:16]}"
        opportunity = db.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
        if opportunity is None:
            opportunity = Opportunity(id=opportunity_id, listing_id=listing.id, product_id=product.id, investigation={})
            db.add(opportunity)
        opportunity.listing_snapshot_id = snapshot.id if snapshot else None
        opportunity.market_snapshot_id = market_snapshot_id
        opportunity.market_cohort_id = product.market_cohort_id
        opportunity.category = product.category
        opportunity.source = source_type
        opportunity.last_pipeline_run_id = run.id
        opportunity.is_current = qualifies
        opportunity.price_edge = edge
        opportunity.final_score = score
        opportunity.market_heat = market.market_heat
        opportunity.heat_band = market.heat_band
        opportunity.confidence = market.confidence
        opportunity.score_breakdown = breakdown
        opportunity.explanation = explanation
        opportunity.investigation_status = "pending" if qualifies else "not_qualified"
        opportunity.computed_at = _now()
        db.flush()
        signal.opportunity_id = opportunity.id
        signal.stage = "confirmed" if qualifies else "withdrawn"
        signal.full_flow_completed = True
        db.add(OpportunityEvaluation(
            opportunity_id=opportunity.id, pipeline_run_id=run.id, listing_id=listing.id,
            product_id=product.id, listing_snapshot_id=snapshot.id if snapshot else None,
            market_snapshot_id=market.id, market_cohort_id=product.market_cohort_id,
            category=product.category, price_edge=edge, final_score=score,
            score_breakdown=breakdown, investigation={},
            status="qualified" if qualifies else "not_qualified", evaluated_at=_now(),
        ))
        if qualifies:
            confirmed += 1
    run.opportunities_found = confirmed


def reanalyze_high_volume_run(run_id: str, db=None) -> dict[str, Any]:
    """Re-run dataset analysis and market consolidation locally without any marketplace I/O."""
    owns_session = db is None
    if owns_session:
        db = SessionLocal()
    try:
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if not run:
            raise ValueError(f"Pipeline run {run_id} not found")
        source_type = ((run.steps or [{}])[0]).get("configuration", {}).get("source_type", "fixture")
        _run_dataset_analysis(db, run, source_type)
        from packages.pipeline.card_projection import project_run_cards, refresh_preliminary_signals
        projection = project_run_cards(db, run.id)
        _run_market_consolidation(db, run, source_type)
        refresh_preliminary_signals(db, run.id)
        state = dict(run.workload_state or {})
        state["market_consolidation_status"] = "completed"
        state.update({"cards_published": projection["published"], "cards_rejected": projection["rejected"], "agent_pending": projection["agent_pending"]})
        state["market_consolidation_error"] = None
        run.workload_state = state
        db.commit()
        return {"status": run.status, "reanalyzed": True, **state}
    finally:
        if owns_session:
            db.close()


def reconcile_historical_runs(*, dry_run: bool = False) -> dict[str, int]:
    """Idempotently repair terminal high-volume read models without model or OLX I/O."""
    db = SessionLocal()
    repaired = failed = 0
    no_direct_found = no_direct_previously_reviewed = eliminated_before_agent = 0
    try:
        runs = db.query(PipelineRun).filter(
            PipelineRun.workload_mode == "high_volume",
            PipelineRun.status.in_(("completed", "completed_partial")),
        ).all()
        for run in runs:
            try:
                settings = _settings(_configuration(run))
                no_direct = db.query(ListingDiscovery).filter(
                    ListingDiscovery.pipeline_run_id == run.id,
                    or_(
                        ListingDiscovery.price.is_(None),
                        ListingDiscovery.price <= 0,
                        ListingDiscovery.price_origin.notin_(("structured", "dom")),
                    ),
                )
                run_no_direct = no_direct.count()
                run_reviewed = no_direct.filter(ListingDiscovery.card_analysis_attempts > 0).count()
                no_direct_found += run_no_direct
                no_direct_previously_reviewed += run_reviewed
                # Historical executions are intentionally deterministic: this
                # repair never creates paid reviews retroactively.
                settings["card_review_mode"] = "disabled"
                from packages.pipeline.card_projection import project_run_cards, refresh_preliminary_signals
                project_run_cards(db, run.id, require_agent=False)
                # A pending historical detail task must never resurrect a card
                # that the new gate just removed.  Keep completed snapshots as
                # historical evidence, but remove them from active read models.
                for task in db.query(ListingEnrichmentTask).join(ListingDiscovery).filter(
                    ListingEnrichmentTask.pipeline_run_id == run.id,
                    ListingEnrichmentTask.status.in_(("pending", "running")),
                    ListingDiscovery.publication_status.in_(("rejected_no_price", "rejected_scope", "rejected_prefilter", "agent_rejected")),
                ).all():
                    task.status = "skipped_unverified"
                    task.error_code = "HISTORICAL_CARD_GATE_REJECTED"
                    task.error_message = "Card removido pela reconciliação do gate de preço."
                    task.finished_at = _now()
                _run_dataset_analysis(db, run, _configuration(run).get("source_type", "fixture"))
                _run_market_consolidation(db, run, _configuration(run).get("source_type", "fixture"))
                refresh_preliminary_signals(db, run.id)
                now = _now()
                terminal_statuses = {
                    "agent_confirmed", "rule_confirmed", "rejected_agent",
                    "rejected_summary", "withheld_unverified", "processed",
                }
                for scope in run.scope_runs:
                    scope.processed_count = sum(item.status in terminal_statuses for item in scope.items)
                    scope.finished_at = scope.finished_at or run.finished_at or now
                    scope.status = "completed" if scope.processed_count == len(scope.items) else "completed_partial"
                state = _refresh_counts(db, run, settings, run.status)
                state["historical_reconciliation"] = {
                    "status": "preview" if dry_run else "completed",
                    "no_direct_price_found": run_no_direct,
                    "no_direct_price_previously_reviewed": run_reviewed,
                    "eliminated_before_agent": run_no_direct,
                }
                run.workload_state = state
                _set_step(run, run.status, state)
                repaired += 1
                eliminated_before_agent += run_no_direct
            except Exception:
                failed += 1
                logger.exception("Historical reconciliation failed for run %s", run.id)
                db.rollback()
        if dry_run:
            db.rollback()
        else:
            db.commit()
        return {
            "scanned": len(runs),
            "repaired": repaired,
            "failed": failed,
            "no_direct_price_found": no_direct_found,
            "no_direct_price_previously_reviewed": no_direct_previously_reviewed,
            "eliminated_before_agent": eliminated_before_agent,
        }
    finally:
        db.close()


def _finalize(
    db,
    run: PipelineRun,
    settings: dict[str, Any],
    *,
    reason: str,
    source_type: str = "fixture",
) -> dict[str, Any]:
    """Close a high-volume run without mutating its preserved task queue."""
    state = _refresh_counts(db, run, settings, "completed_partial")
    pending_details = int(state.get("enrichment_pending", 0))
    pending_retrievals = int(state.get("retrieval_pending", 0))
    deferred_retrievals = db.query(PipelineRetrievalTask).filter(
        PipelineRetrievalTask.pipeline_run_id == run.id,
        PipelineRetrievalTask.status == "deferred",
    ).count()
    failed_details = int(state.get("enrichment_failed", 0))
    failed_reviews = int(state.get("agent_failed", 0)) if settings.get("card_review_mode") == "required" else 0
    partial = bool(pending_details or pending_retrievals or deferred_retrievals or failed_details or failed_reviews)
    target_status = "completed_partial" if partial else "completed"

    _record_state_transition(
        db,
        run,
        to_status=target_status,
        reason_code="workload_finalized",
        message=reason,
    )
    _finalize_attempt(db, run, target_status, summary=reason)

    run.finished_at = _now()
    if run.started_at:
        run.duration_seconds = round(max(0.0, (run.finished_at - run.started_at).total_seconds()), 3)
    run.workload_not_before = None
    state["phase"] = run.status
    state["not_before"] = None
    state["pending_detail_tasks"] = max(0, pending_details)
    state["pending_retrieval_tasks"] = max(0, pending_retrievals)
    state["deferred_retrieval_tasks"] = max(0, deferred_retrievals)
    state["message"] = reason
    run.workload_state = state
    _set_step(run, run.status, state)

    # Phase 2: Decoupled Dataset analysis
    try:
        _run_dataset_analysis(db, run, source_type)
        state["dataset_analysis_status"] = "completed"
    except Exception as exc:
        state["dataset_analysis_status"] = "failed"
        state["dataset_analysis_error"] = f"{type(exc).__name__}: {exc}"
        logger.exception("Dataset analysis failed for run %s", run.id)

    # Phase 3: deterministic card projection.  It does not navigate OLX and
    # makes useful cards visible before optional detail enrichment.
    try:
        from packages.pipeline.card_projection import project_run_cards
        projection = project_run_cards(
            db, run.id,
            require_agent=settings.get("card_review_mode") == "required",
        )
        state.update({
            "cards_published": projection["published"],
            "cards_rejected": projection["rejected"],
            "cards_withheld": projection["withheld"],
            "agent_pending": projection["agent_pending"],
        })
        db.commit()
    except Exception as exc:
        state["card_projection_status"] = "failed"
        state["card_projection_error"] = f"{type(exc).__name__}: {exc}"
        logger.exception("Card projection failed for run %s", run.id)

    # Phase 4: Market snapshot consolidation
    try:
        _run_market_consolidation(db, run, source_type)
        _reconcile_opportunity_signals(db, run, source_type)
        state["market_consolidation_status"] = "completed"
    except Exception as exc:
        state["market_consolidation_status"] = "failed"
        state["market_consolidation_error"] = f"{type(exc).__name__}: {exc}"
        logger.exception("Market consolidation failed for run %s", run.id)

    # Phase 5: score the projected preliminary market.  The model queue only
    # receives the ambiguous cards marked by the projection.
    try:
        from packages.pipeline.card_projection import refresh_preliminary_signals
        state["preliminary_opportunities"] = refresh_preliminary_signals(db, run.id)
    except Exception as exc:
        logger.exception("Preliminary opportunity scoring failed for run %s", run.id)

    terminal_item_statuses = {
        "agent_confirmed", "rule_confirmed", "rejected_agent",
        "rejected_summary", "withheld_unverified", "processed",
    }
    now = _now()
    for scope in run.scope_runs:
        scope.processed_count = sum(item.status in terminal_item_statuses for item in scope.items)
        scope.finished_at = scope.finished_at or now
        scope.status = "completed" if scope.processed_count == len(scope.items) else "completed_partial"

    # Re-read all counters after projection and analytics so the public run
    # cannot retain the pre-finalization zeroes.
    state = _refresh_counts(db, run, settings, run.status)
    state.update({
        "dataset_analysis_status": "completed" if not state.get("dataset_analysis_error") else "failed",
        "market_consolidation_status": "completed" if not state.get("market_consolidation_error") else "failed",
        "message": reason,
        "pending_detail_tasks": max(0, pending_details),
        "pending_retrieval_tasks": max(0, pending_retrievals),
        "deferred_retrieval_tasks": max(0, deferred_retrievals),
        "not_before": None,
    })
    run.workload_state = state
    _set_step(run, run.status, state)
    db.commit()
    return {"status": run.status, **state}



def finalize_high_volume_run(run_id: str, *, reason: str | None = None) -> dict[str, Any]:
    """Idempotently close an existing high-volume workload without OLX I/O."""
    db = SessionLocal()
    try:
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if run is None:
            raise ValueError("Pipeline run not found")
        if run.workload_mode != "high_volume":
            raise ValueError("Only high-volume runs can be finalized here")
        configuration = _configuration(run)
        settings = _settings(configuration)
        source_type = configuration.get("source_type", "fixture")
        if run.status in {"completed", "completed_partial", "failed", "blocked"}:
            state = _refresh_counts(db, run, settings, run.status)
            state["not_before"] = None
            run.workload_state = state
            db.commit()
            return {"status": run.status, **state, "reused": True}
        return {
            **_finalize(
                db,
                run,
                settings,
                reason=reason or "Execução encerrada parcialmente; dados coletados e detalhes pendentes foram preservados.",
                source_type=source_type,
            ),
            "reused": False,
        }
    finally:
        db.close()


def resume_high_volume_run(
    run_id: str,
    *,
    include_failed: bool = False,
    actor: str = "operator",
) -> dict[str, Any]:
    """Resume pending detail enrichment tasks for a high-volume run without repeating searches."""
    db = SessionLocal()
    try:
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if not run:
            raise ValueError(f"Pipeline run {run_id} not found")
        if run.workload_mode != "high_volume":
            raise ValueError("Only high-volume runs can be resumed")
        if run.status not in {"completed_partial", "waiting_worker", "waiting_budget", "failed", "queued"}:
            raise ValueError(f"Run in status '{run.status}' cannot be resumed")

        settings = _settings(_configuration(run))
        now = _now()

        last_attempt = (
            db.query(PipelineWorkloadAttempt)
            .filter(PipelineWorkloadAttempt.pipeline_run_id == run.id)
            .order_by(PipelineWorkloadAttempt.attempt_number.desc())
            .first()
        )
        attempt_num = (last_attempt.attempt_number + 1) if last_attempt else 1

        new_attempt = PipelineWorkloadAttempt(
            id=f"attempt-{uuid.uuid4().hex[:20]}",
            pipeline_run_id=run.id,
            attempt_number=attempt_num,
            reason="operator_resume",
            status="running",
            started_at=now,
            deadline_at=now + timedelta(minutes=settings["duration_minutes"]),
            worker_revision=os.getenv("BUILD_SHA", "dev"),
            parser_version="olx-dom-2026-08-24",
        )
        db.add(new_attempt)

        enrichment_query = db.query(ListingEnrichmentTask).filter(
            ListingEnrichmentTask.pipeline_run_id == run.id,
        )
        if include_failed:
            target_tasks = enrichment_query.filter(
                ListingEnrichmentTask.status.in_(("pending", "failed", "skipped_unverified"))
            ).all()
        else:
            target_tasks = enrichment_query.filter(ListingEnrichmentTask.status == "pending").all()

        for t in target_tasks:
            t.status = "pending"
            t.lease_owner = None
            t.lease_token = None
            t.lease_expires_at = None
            t.heartbeat_at = None
            t.not_before = None

        state = dict(run.workload_state or {})
        state["parser_circuit_open"] = False
        state["parser_consecutive_failures"] = 0
        state["circuit_trip_reason"] = None

        _record_state_transition(
            db,
            run,
            to_status="queued",
            reason_code="operator_resume",
            attempt_number=attempt_num,
            actor=actor,
            message="Operador retomou o enriquecimento dos detalhes pendentes.",
        )

        run.workload_deadline = now + timedelta(minutes=settings["duration_minutes"])
        run.workload_not_before = None
        run.finished_at = None
        run.error_code = None
        run.error_message = None

        state = _refresh_counts(db, run, settings, "enrichment")
        state["not_before"] = None
        run.workload_state = state
        _set_step(run, "queued", state)

        db.commit()
        return {"status": run.status, "attempt_number": attempt_num, **state}
    finally:
        db.close()


def reap_stale_tasks_and_workloads(db, stale_tolerance_seconds: int = 30) -> dict[str, int]:
    """Recover expired leases and orphaned running runs."""
    now = _now()
    cutoff = now - timedelta(seconds=stale_tolerance_seconds)
    reaped_tasks = 0
    reaped_runs = 0

    # 1. Check expired retrieval tasks
    stale_retrievals = db.query(PipelineRetrievalTask).filter(
        PipelineRetrievalTask.status == "running",
        PipelineRetrievalTask.lease_expires_at <= cutoff,
    ).all()
    for task in stale_retrievals:
        if task.attempts >= task.max_attempts:
            task.status = "failed"
            task.last_error_code = "lease_expired_max_attempts"
            task.last_error_at = now
            task.finished_at = now
        else:
            task.status = "pending"
            task.lease_owner = None
            task.lease_token = None
            task.lease_expires_at = None
            task.not_before = now + timedelta(seconds=10)
        reaped_tasks += 1

    # 2. Check expired enrichment tasks
    stale_enrichments = db.query(ListingEnrichmentTask).filter(
        ListingEnrichmentTask.status == "running",
        ListingEnrichmentTask.lease_expires_at <= cutoff,
    ).all()
    for task in stale_enrichments:
        if task.attempts >= task.max_attempts:
            task.status = "failed"
            task.last_error_code = "lease_expired_max_attempts"
            task.last_error_at = now
            task.finished_at = now
        else:
            task.status = "pending"
            task.lease_owner = None
            task.lease_token = None
            task.lease_expires_at = None
            task.not_before = now + timedelta(seconds=10)
        reaped_tasks += 1

    # 3. Check high-volume runs stuck in 'running' or 'claimed' where no task has an active lease
    stuck_runs = db.query(PipelineRun).filter(
        PipelineRun.workload_mode == "high_volume",
        PipelineRun.status.in_(("running", "claimed")),
    ).all()
    for r in stuck_runs:
        has_active_retrieval = db.query(PipelineRetrievalTask).filter(
            PipelineRetrievalTask.pipeline_run_id == r.id,
            PipelineRetrievalTask.status == "running",
            PipelineRetrievalTask.lease_expires_at > cutoff,
        ).first() is not None
        has_active_enrichment = db.query(ListingEnrichmentTask).filter(
            ListingEnrichmentTask.pipeline_run_id == r.id,
            ListingEnrichmentTask.status == "running",
            ListingEnrichmentTask.lease_expires_at > cutoff,
        ).first() is not None

        if not has_active_retrieval and not has_active_enrichment:
            if r.workload_deadline and now >= r.workload_deadline:
                configuration = _configuration(r)
                settings = _settings(configuration)
                _finalize(
                    db,
                    r,
                    settings,
                    reason="Execução finalizada pelo reaper após expiração do prazo.",
                    source_type=configuration.get("source_type", "fixture"),
                )
            else:
                _record_state_transition(
                    db,
                    r,
                    to_status="queued",
                    reason_code="lease_reaped",
                    actor="scheduler",
                    message="Execução re-enfileirada pelo reaper após expiração de lease inativa.",
                )
            reaped_runs += 1

    if reaped_tasks or reaped_runs:
        db.commit()

    return {"reaped_tasks": reaped_tasks, "reaped_runs": reaped_runs}


def _queue(
    db,
    run: PipelineRun,
    settings: dict[str, Any],
    phase: str,
    when: datetime,
    *,
    status: str = "queued",
    reason_code: str = "scheduled_pace",
    message: str | None = None,
) -> None:
    _record_state_transition(db, run, to_status=status, reason_code=reason_code, message=message)
    run.workload_not_before = when
    state = _state(run, settings, status if status != "queued" else phase)
    state["not_before"] = when.isoformat()
    if message:
        state["message"] = message
    run.workload_state = state


def _record_navigation(db, run_id: str, task_kind: str, task_id: str, scheduled_at: datetime | None) -> None:
    db.add(PipelineNavigationLedger(
        id=f"nav-{uuid.uuid4().hex[:20]}",
        pipeline_run_id=run_id,
        task_kind=task_kind,
        task_id=task_id,
        status="completed",
        scheduled_at=scheduled_at,
        occurred_at=_now(),
    ))


def _retry_at(error: OlxAccessError) -> datetime:
    reset_at = error.detail.get("reset_at") if isinstance(error.detail, dict) else None
    if isinstance(reset_at, str):
        try:
            return datetime.fromisoformat(reset_at.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    retry_after = error.detail.get("retry_after_seconds") if isinstance(error.detail, dict) else None
    try:
        return _now() + timedelta(seconds=max(1, int(retry_after)))
    except (TypeError, ValueError):
        return _now() + timedelta(minutes=5)


def _scope_product(db, scope: PipelineRunScope) -> Product | None:
    product_id = (scope.scope_snapshot or {}).get("product_id")
    return db.query(Product).filter(Product.id == product_id).first() if product_id else None


async def _initialize(db, run: PipelineRun, settings: dict[str, Any], source_type: str, query: str, limit: int) -> None:
    if db.query(PipelineRunScope).filter(PipelineRunScope.pipeline_run_id == run.id).count():
        return
    for index, scope_data in enumerate(_run_scopes(_configuration(run), source_type, query, limit), start=1):
        scope = PipelineRunScope(
            id=f"{run.id}-scope-{index}-{uuid.uuid4().hex[:6]}",
            pipeline_run_id=run.id,
            search_scope_id=scope_data.get("id"),
            scope_snapshot=scope_data,
            status="running",
            started_at=_now(),
        )
        db.add(scope)
        db.flush()
        db.add(PipelineRetrievalTask(
            id=f"retrieve-{uuid.uuid4().hex[:20]}",
            pipeline_run_id=run.id,
            pipeline_run_scope_id=scope.id,
            page=1,
            priority=index,
            status="pending",
            max_attempts=3,
        ))
    run.workload_mode = "high_volume"
    # New runs receive an exact, wall-clock deadline at enqueue time.  Keep
    # legacy runs compatible by only supplying one when absent.
    run.workload_deadline = run.workload_deadline or (_now() + timedelta(minutes=settings["duration_minutes"]))
    run.workload_not_before = None
    run.workload_state = _state(run, settings, "discovery")
    _get_or_create_active_attempt(db, run, reason="initial")
    db.commit()


def _summary_priority(price: float | None, price_origin: str, title: str, location: str = "") -> float:
    """Deterministic score prioritizing confirmed price, main hardware items, and location."""
    if price is not None and price > 0 and price_origin in {"structured", "dom"}:
        price_component = 50_000.0
    else:
        price_component = 10_000.0

    title_lower = (title or "").lower()
    is_part_or_acc = any(
        w in title_lower
        for w in (
            "suporte", "capa", "cabo", "skin", "carregador", "fone", "headset",
            "cooler", "carcaça", "carcaca", "defeito", "peças", "pecas", "sucata",
            "compro", "procuro", "conserto", "assistência", "assistencia", "jogo",
            "midia fisica", "fita", "cartucho", "caixa vazia", "apenas caixa",
            "apenas controle", "somente controle"
        )
    )
    is_main_item = any(
        w in title_lower
        for w in (
            "ps5", "playstation 5", "play 5", "ps4", "playstation 4", "console",
            "xbox", "nintendo switch", "steam deck", "rog ally",
            "notebook", "laptop", "macbook", "galaxy book", "thinkpad", "ideapad", "nitro 5", "dell g15",
            "rtx", "gtx", "radeon rx", "rx 6", "rx 7", "placa de video",
            "ryzen", "core i3", "core i5", "core i7", "core i9", "processador",
            "ddr4", "ddr5", "memoria ram", "sodimm", "nvme", "ssd m.2"
        )
    )
    if is_part_or_acc:
        type_bonus = -100_000.0
    elif is_main_item:
        type_bonus = 50_000.0
    else:
        type_bonus = 10_000.0

    title_len_bonus = min(len(title or ""), 120) * 10.0
    location_bonus = 500.0 if location else 0.0
    return price_component + type_bonus + title_len_bonus + location_bonus


def claim_next_retrieval_task(db, run_id: str, worker_id: str, lease_seconds: int = 120) -> PipelineRetrievalTask | None:
    now = _now()
    query = (
        db.query(PipelineRetrievalTask)
        .filter(
            PipelineRetrievalTask.pipeline_run_id == run_id,
            PipelineRetrievalTask.status == "pending",
            or_(PipelineRetrievalTask.not_before.is_(None), PipelineRetrievalTask.not_before <= now),
            or_(PipelineRetrievalTask.lease_expires_at.is_(None), PipelineRetrievalTask.lease_expires_at <= now),
        )
        .order_by(PipelineRetrievalTask.priority.asc(), PipelineRetrievalTask.created_at.asc())
    )
    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    task = query.first()
    if not task:
        return None
    task.status = "running"
    task.lease_owner = worker_id
    task.lease_token = uuid.uuid4().hex
    task.lease_expires_at = now + timedelta(seconds=lease_seconds)
    task.heartbeat_at = now
    task.attempts += 1
    task.started_at = now
    db.commit()
    return task


def claim_next_enrichment_task(db, run_id: str, worker_id: str, lease_seconds: int = 120) -> ListingEnrichmentTask | None:
    now = _now()
    query = (
        db.query(ListingEnrichmentTask)
        .filter(
            ListingEnrichmentTask.pipeline_run_id == run_id,
            ListingEnrichmentTask.status == "pending",
            or_(ListingEnrichmentTask.not_before.is_(None), ListingEnrichmentTask.not_before <= now),
            or_(ListingEnrichmentTask.lease_expires_at.is_(None), ListingEnrichmentTask.lease_expires_at <= now),
        )
        .order_by(ListingEnrichmentTask.priority.asc(), ListingEnrichmentTask.created_at.asc())
    )
    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    task = query.first()
    if not task:
        return None
    task.status = "running"
    task.lease_owner = worker_id
    task.lease_token = uuid.uuid4().hex
    task.lease_expires_at = now + timedelta(seconds=lease_seconds)
    task.heartbeat_at = now
    task.attempts += 1
    task.started_at = now
    db.commit()
    return task


async def _process_retrieval(
    db,
    run: PipelineRun,
    task: PipelineRetrievalTask,
    source,
    source_type: str,
    settings: dict[str, Any],
) -> None:
    from packages.pipeline.runner import _is_recommendation_external_id, _summary_matches_scope

    scope = task.scope_run
    scope_data = dict(scope.scope_snapshot or {})
    product = _scope_product(db, scope)

    summaries = await source.search(SearchQuery(
        query=str(scope_data.get("query", "")),
        category=scope_data.get("category"),
        min_price=scope_data.get("min_price"),
        max_price=scope_data.get("max_price"),
        olx_pay_only=bool(scope_data.get("olx_pay_only")),
        delivery_only=bool(scope_data.get("delivery_only")),
        require_price=bool(scope_data.get("require_price", True)),
        sort=scope_data.get("sort", "recent"),
        limit=settings["page_size"],
        page=task.page,
    ))
    diagnostics = getattr(source, "last_search_diagnostics", {}) or {}
    scope.search_url = getattr(source, "last_search_url", "") or scope.search_url
    scope.total_results = max(scope.total_results, int(diagnostics.get("total_results", 0)))
    scope.cards_detected += int(diagnostics.get("cards_detected", len(summaries)))
    scope.items_returned += len(summaries)
    task.observed_count = len(summaries)
    created = 0
    now = _now()
    for rank, summary in enumerate(summaries, start=1):
        canonical_key = str(summary.marketplace_item_id or summary.url or summary.external_id)
        existing = db.query(ListingDiscovery).filter(
            ListingDiscovery.pipeline_run_id == run.id,
            ListingDiscovery.canonical_key == canonical_key,
        ).first()
        if existing:
            existing.last_observed_at = now
            continue
        matching_title = (
            True
            if scope_data.get("collection_mode")
            else _summary_matches_scope(summary.title, scope_data, product)
        )
        already_known = db.query(Listing.id).filter(
            Listing.source == source_type,
            Listing.external_id == summary.external_id,
        ).first() is not None
        if source_type == "olx" and _is_recommendation_external_id(summary.external_id):
            triage_status, reason = "rejected_summary", "Resultado recomendado fora da busca."
        elif not matching_title:
            triage_status, reason = "rejected_summary", "Título não corresponde ao escopo."
        elif already_known:
            triage_status, reason = "history_only", "Anúncio já observado; mantido no histórico."
        else:
            triage_status, reason = "detail_candidate", "Resumo elegível para validar evidências obrigatórias."

        price_val = summary.price
        price_orig = getattr(summary, "price_origin", "dom" if price_val is not None else "missing")
        loc_val = summary.location
        loc_orig = getattr(summary, "location_origin", "dom" if loc_val else "missing")

        discovery = ListingDiscovery(
            id=f"discovery-{uuid.uuid4().hex[:20]}",
            pipeline_run_id=run.id,
            pipeline_retrieval_task_id=task.id,
            pipeline_run_scope_id=scope.id,
            canonical_key=canonical_key,
            external_id=summary.external_id,
            marketplace_item_id=summary.marketplace_item_id,
            normalized_url=summary.url,
            title=summary.title,
            price=price_val,
            price_origin=price_orig,
            price_raw=str(getattr(summary, "price_raw", "") or ""),
            location=loc_val,
            location_origin=loc_orig,
            condition=summary.condition,
            seller=summary.seller,
            raw_summary=summary.model_dump(),
            field_coverage=getattr(summary, "field_coverage", {}),
            parser_version=getattr(summary, "parser_version", "olx-dom-2026-08-24"),
            triage_status=triage_status,
            triage_reason=reason,
            priority_score=_summary_priority(price_val, price_orig, summary.title, loc_val),
            first_observed_at=now,
            last_observed_at=now,
            scope_run=scope,
        )
        db.add(discovery)
        # Persist the raw observation and the cheap decision together.  A
        # rejection is audit data, not a dropped discovery.
        from packages.pipeline.card_gate import apply_cheap_gate
        apply_cheap_gate(discovery)
        db.add(PipelineRunScopeItem(
            pipeline_run_scope_id=scope.id,
            external_id=summary.external_id,
            rank=(task.page - 1) * settings["page_size"] + rank,
            status=triage_status,
            error_message=None if triage_status == "detail_candidate" else reason,
        ))
        created += 1

    task.discovered_count = created
    task.status = "completed"
    task.lease_owner = None
    task.lease_expires_at = None
    task.finished_at = _now()
    scope.processed_count = db.query(PipelineRunScopeItem).filter(
        PipelineRunScopeItem.pipeline_run_scope_id == scope.id,
        PipelineRunScopeItem.status == "processed",
    ).count()

    discovered = db.query(ListingDiscovery).filter(ListingDiscovery.pipeline_run_id == run.id).count()
    if (
        len(summaries) > 0
        and discovered < settings["discovery_target"]
        and task.page < settings["max_discovery_pages"]
        and not db.query(PipelineRetrievalTask).filter(
            PipelineRetrievalTask.pipeline_run_id == run.id,
            PipelineRetrievalTask.pipeline_run_scope_id == scope.id,
            PipelineRetrievalTask.page == task.page + 1,
        ).first()
    ):
        db.add(PipelineRetrievalTask(
            id=f"retrieve-{uuid.uuid4().hex[:20]}",
            pipeline_run_id=run.id,
            pipeline_run_scope_id=scope.id,
            page=task.page + 1,
            priority=task.priority + 10,
            status="pending",
            max_attempts=3,
        ))
    _record_navigation(db, run.id, "retrieval", task.id, task.started_at)
    db.commit()


def _plan_enrichment(db, run: PipelineRun, settings: dict[str, Any]) -> None:
    if settings.get("detail_access_mode") == "disabled":
        return
    if db.query(ListingEnrichmentTask).filter(ListingEnrichmentTask.pipeline_run_id == run.id).count():
        return
    candidates_query = db.query(ListingDiscovery).filter(
        ListingDiscovery.pipeline_run_id == run.id,
        ListingDiscovery.triage_status == "detail_candidate",
        ListingDiscovery.publication_status.in_(("approved", "agent_approved")),
    )
    if settings.get("signal_threshold_enabled"):
        candidates_query = candidates_query.join(
            OpportunitySignal, OpportunitySignal.listing_discovery_id == ListingDiscovery.id,
        ).filter(
            OpportunitySignal.stage == "preliminary",
            OpportunitySignal.preliminary_score >= settings.get("enrichment_threshold", 70),
        )
    candidates = candidates_query.order_by(
        ListingDiscovery.priority_score.desc(), ListingDiscovery.first_observed_at.asc(),
    ).limit(settings["enrichment_cap"]).all()
    for rank, discovery in enumerate(candidates, start=1):
        db.add(ListingEnrichmentTask(
            id=f"enrich-{uuid.uuid4().hex[:20]}",
            pipeline_run_id=run.id,
            listing_discovery_id=discovery.id,
            status="pending",
            priority=rank,
            max_attempts=3,
        ))
    db.commit()


def prepare_card_review_stage(db, run: PipelineRun, settings: dict[str, Any]) -> bool:
    """Gate every candidate and pause OLX time while required reviews run.

    Returns true while the pipeline must yield to the card-agent worker.
    """
    from packages.pipeline.card_projection import project_run_cards

    required = settings.get("card_review_mode") == "required"
    project_run_cards(db, run.id, require_agent=required, publish=False)
    if not required:
        return False

    candidates = db.query(ListingDiscovery).filter(
        ListingDiscovery.pipeline_run_id == run.id,
        ListingDiscovery.gate_status == "agent_eligible",
    ).all()
    from packages.pipeline.discovery_card_analysis import _queue_eligible_cards
    _queue_eligible_cards(candidates)

    active = sum(item.card_analysis_status in {"pending", "running"} for item in candidates)
    if active:
        state = dict(run.workload_state or {})
        if run.workload_deadline:
            state["olx_seconds_remaining"] = max(0, int((run.workload_deadline - _now()).total_seconds()))
        run.workload_deadline = None
        state["deadline_at"] = None
        state["phase"] = "reviewing_cards"
        run.workload_state = state
        _record_state_transition(
            db, run, "reviewing_cards", "card_review_required",
            message="Revisão opcional por agente em andamento antes da publicação e dos anúncios.",
        )
        _set_step(run, "reviewing_cards", _refresh_counts(db, run, settings, "reviewing_cards"))
        db.commit()
        return True
    return False


def complete_card_review_stage(db, run: PipelineRun) -> bool:
    """Resume the pipeline after all required reviews reached a terminal state."""
    settings = _settings(_configuration(run))
    if settings.get("card_review_mode") != "required":
        return False
    active = db.query(ListingDiscovery).filter(
        ListingDiscovery.pipeline_run_id == run.id,
        ListingDiscovery.card_analysis_status.in_(("pending", "running")),
    ).count()
    if active:
        return False
    from packages.pipeline.card_projection import project_run_cards

    project_run_cards(db, run.id, require_agent=True, publish=False)
    state = _refresh_counts(db, run, settings, "queued")
    remaining = max(0, int(state.get("olx_seconds_remaining", settings["duration_minutes"] * 60)))
    run.workload_deadline = _now() + timedelta(seconds=remaining)
    state["deadline_at"] = run.workload_deadline.isoformat()
    run.workload_state = state
    _record_state_transition(db, run, "queued", "card_review_completed", message="Revisão dos cards concluída.")
    _set_step(run, "queued", state)
    db.flush()
    return True


def schedule_signal_enrichment(
    run_id: str,
    *,
    threshold: float = 70.0,
    max_items: int = 100,
    min_price: float | None = None,
    max_price: float | None = None,
    category: str | None = None,
    location: str | None = None,
    actor: str = "operator",
) -> dict[str, Any]:
    """Create detail tasks from persisted signals without repeating retrieval."""
    db = SessionLocal()
    try:
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if not run or run.workload_mode != "high_volume":
            raise ValueError("High-volume pipeline not found")
        query = (
            db.query(ListingDiscovery, OpportunitySignal)
            .outerjoin(OpportunitySignal, OpportunitySignal.listing_discovery_id == ListingDiscovery.id)
            .filter(
                ListingDiscovery.pipeline_run_id == run_id,
                ListingDiscovery.publication_status.in_(("approved", "agent_approved")),
            )
        )
        if threshold is not None and threshold > 0:
            query = query.filter(
                or_(
                    OpportunitySignal.preliminary_score >= max(0.0, min(float(threshold), 100.0)),
                    and_(OpportunitySignal.id.is_(None), ListingDiscovery.priority_score >= max(0.0, min(float(threshold), 100.0))),
                )
            )
        if min_price is not None:
            query = query.filter(or_(ListingDiscovery.price.is_(None), ListingDiscovery.price >= min_price))
        if max_price is not None:
            query = query.filter(or_(ListingDiscovery.price.is_(None), ListingDiscovery.price <= max_price))
        if location:
            query = query.filter(ListingDiscovery.location.ilike(f"%{location}%"))
        rows = query.order_by(OpportunitySignal.preliminary_score.desc().nullslast(), ListingDiscovery.priority_score.desc()).all()

        if category:
            rows = [row for row in rows if str((row[0].card_analysis_result or {}).get("category") or "other") == category]
        rows = rows[:max(1, min(max_items, 100))]
        created = 0
        for rank, (discovery, signal) in enumerate(rows, start=1):
            existing = db.query(ListingEnrichmentTask.id).filter(
                ListingEnrichmentTask.pipeline_run_id == run_id,
                ListingEnrichmentTask.listing_discovery_id == discovery.id,
            ).first()
            if existing:
                continue
            db.add(ListingEnrichmentTask(
                id=f"enrich-{uuid.uuid4().hex[:20]}", pipeline_run_id=run_id,
                listing_discovery_id=discovery.id, status="pending", priority=rank, max_attempts=3,
            ))
            if signal:
                signal.stage = "queued_for_enrichment"
                signal.updated_at = _now()
            created += 1
        if created:
            settings = _settings(_configuration(run))
            run.finished_at = None
            run.workload_deadline = _now() + timedelta(minutes=settings["duration_minutes"])
            run.workload_not_before = None
            state = _refresh_counts(db, run, settings, "enrichment")
            state["enrichment_threshold"] = threshold
            run.workload_state = state
            _record_state_transition(db, run, "queued", "enrichment_selected", actor=actor)
        db.commit()
        return {"queued": created, "eligible": len(rows), "threshold": threshold, "status": run.status}
    finally:
        db.close()


async def _normalize(gateway, detail, category_hint: str | None, *, run_id: str, attempt_number: int, task_id: str):
    context = {
        "delivery_text": detail.delivery_text,
        "delivery_evidence": [detail.delivery_text] if detail.delivery_text else [],
        "seller_verification": detail.seller_verification,
        "seller_evidence": detail.seller_evidence,
        "seller_rating": detail.seller_rating,
        "attributes": detail.attributes,
    }
    with model_metric_context(
        pipeline_run_id=run_id,
        attempt_number=attempt_number,
        task_kind="enrichment",
        task_id=task_id,
        origin="pipeline",
        operation="normalize_listing",
    ):
        try:
            res = await gateway.normalize_listing(
                detail.title, detail.description, category_hint=category_hint, listing_context=context
            )
            return res, "completed", None, "full"
        except Exception as exc:
            fallback = DeterministicLocalModelGateway()
            res = await fallback.normalize_listing(
                detail.title, detail.description, category_hint=category_hint, listing_context=context
            )
            return res, "degraded", f"{type(exc).__name__}: {exc}", "degraded"


async def _process_enrichment(
    db,
    run: PipelineRun,
    task: ListingEnrichmentTask,
    source,
    source_type: str,
    attempt: PipelineWorkloadAttempt,
) -> None:
    from packages.classification.eligibility import evaluate_analytics_eligibility
    from packages.pipeline.runner import _resolve_product

    discovery = task.discovery
    from packages.pipeline.card_gate import apply_cheap_gate
    cheap = apply_cheap_gate(discovery)
    if not cheap.approved or discovery.publication_status not in {"approved", "agent_approved"}:
        task.status = "skipped_unverified"
        task.error_code = cheap.reason_code or "CARD_NOT_PUBLICATION_ELIGIBLE"
        task.error_message = "Card não passou pelo gate de publicação antes de detalhes."
        task.finished_at = _now()
        task.lease_owner = None
        task.lease_expires_at = None
        db.commit()
        return
    scope = discovery.scope_run
    scope_data = dict(scope.scope_snapshot or {}) if scope else {}
    app_mode = os.getenv("APP_MODE", "local").lower()
    model_mode = _configuration(run).get("model_mode", "auto")
    gateway = DeterministicLocalModelGateway() if model_mode == "local" or (model_mode == "auto" and app_mode == "local") else get_model_gateway()

    detail = await source.fetch_listing(discovery.external_id)

    normalized, normalization_status, normalization_error, quality = await _normalize(
        gateway,
        detail,
        # A broad collection has no product category assertion.  Keep items
        # that do not fit the taxonomy as ``other`` instead of borrowing the
        # query's category as a fallback identity.
        None if scope_data.get("collection_mode") else scope_data.get("category"),
        run_id=run.id,
        attempt_number=attempt.attempt_number,
        task_id=task.id,
    )

    match_status = "confirmed"
    match_details: dict[str, Any] = {}
    plan_payload = scope_data.get("plan")
    if scope_data.get("collection_mode"):
        match_status = "collected"
        match_details = {
            "status": "collected",
            "reasons": ["Coleta ampla sem requisitos de match nesta execução."],
        }
    elif isinstance(plan_payload, dict) and plan_payload.get("schema_version"):
        try:
            plan = SearchPlanV1.model_validate(plan_payload)
            candidate = SearchCandidate(
                source=source_type,
                external_id=str(detail.external_id),
                title=detail.title,
                price=detail.price,
                location=", ".join(value for value in (detail.location_city, detail.location_state) if value),
                condition=detail.condition,
                url=detail.source_url,
                description=detail.description,
                attributes={**(normalized.extracted_attributes or {}), **(detail.attributes or {})},
            )
            match = evaluate_candidate(candidate, plan)
            match_status = match.status
            match_details = match.model_dump()
        except (TypeError, ValueError) as exc:
            match_status = "unverified"
            match_details = {"status": "unverified", "reasons": [f"Plano não pôde ser aplicado: {type(exc).__name__}"]}

    canonical_product = None
    if scope_data.get("catalog_match"):
        from packages.catalog import find_catalog_notebook

        canonical_product = find_catalog_notebook(
            db, title=detail.title, description=detail.description,
            attributes={**(normalized.extracted_attributes or {}), **(detail.attributes or {})},
        )
        if canonical_product is None:
            match_status = "unverified"
            match_details = {
                **match_details, "status": "unverified", "catalog": {"status": "unverified"},
                "reasons": [*(match_details.get("reasons") or []), "Modelo não identificado no catálogo de notebooks."],
            }
        else:
            match_details = {**match_details, "catalog": {"status": "confirmed", "product_name": canonical_product.display_name}}

    product = _resolve_product(
        db,
        normalized,
        detail,
        canonical_product=None if scope_data.get("collection_mode") else (canonical_product or (_scope_product(db, scope) if scope else None)),
    )
    listing = db.query(Listing).filter(
        Listing.source == source_type,
        Listing.external_id == detail.external_id,
    ).first()

    eligibility = evaluate_analytics_eligibility(
        category=normalized.category,
        item_form=normalized.item_form,
        classification_status=normalized.classification_status,
        price=detail.price,
        condition=detail.condition,
        attributes={**(normalized.extracted_attributes or {}), **(detail.attributes or {})},
    )

    now = _now()
    if listing is None:
        listing = Listing(
            id=f"{source_type}-{hashlib.sha1(detail.external_id.encode('utf-8')).hexdigest()[:16]}",
            source=source_type,
            external_id=detail.external_id,
            first_seen=now,
            created_at=now,
        )
        db.add(listing)

    listing.marketplace_item_id = detail.marketplace_item_id
    listing.product_id = product.id
    listing.market_cohort_id = product.market_cohort_id
    listing.category = normalized.category
    listing.item_form = normalized.item_form
    listing.classification_status = normalized.classification_status
    listing.classification_confidence = normalized.confidence
    listing.taxonomy_version = normalized.taxonomy_version
    listing.schema_version = normalized.schema_version
    listing.analytics_eligible = eligibility.analytics_eligible
    listing.exclusion_codes = eligibility.exclusion_codes
    listing.classification_evidence = normalized.classification_evidence
    listing.title = detail.title
    listing.description = detail.description
    listing.price = detail.price
    listing.location_state = detail.location_state
    listing.location_city = detail.location_city
    listing.seller_name = detail.seller_name
    listing.seller_rating = detail.seller_rating
    listing.condition = detail.condition
    listing.attributes = {**(normalized.extracted_attributes or {}), **(detail.attributes or {})}
    listing.delivery_status = normalized.delivery_status
    listing.delivery_evidence = normalized.delivery_evidence
    listing.seller_verification = normalized.seller_verification
    listing.seller_signal_level = normalized.seller_signal_level
    listing.seller_evidence = normalized.seller_evidence
    listing.analysis_summary = normalized.listing_summary
    listing.analysis_status = normalization_status
    listing.analysis_updated_at = now
    listing.source_url = detail.source_url
    listing.last_seen = now
    listing.status = "active"
    listing.evidence_level = "detail"
    listing.audit_status = "audited"
    listing.evidence_provenance = {"detail": "listing_page", "classification": "detail_normalization"}
    listing.last_audited_at = now
    listing.normalization_confidence = normalized.confidence
    db.flush()

    task.listing_id = listing.id
    task.status = "completed"
    task.lease_owner = None
    task.lease_expires_at = None
    task.finished_at = now
    signal = db.query(OpportunitySignal).filter(OpportunitySignal.listing_discovery_id == discovery.id).first()
    if signal:
        signal.stage = "enriched_pending_scoring"
        signal.updated_at = now
    discovery.triage_status = "enriched" if match_status in {"confirmed", "collected"} else "unverified"
    discovery.triage_reason = (
        "Detalhe coletado para filtragem posterior." if match_status == "collected"
        else "Detalhe coletado." if match_status == "confirmed"
        else "Evidência obrigatória não confirmada."
    )

    if scope:
        scope_item = db.query(PipelineRunScopeItem).filter(
            PipelineRunScopeItem.pipeline_run_scope_id == scope.id,
            PipelineRunScopeItem.external_id == discovery.external_id,
        ).first()
        if scope_item:
            scope_item.listing_id = listing.id
            scope_item.status = "processed"
            scope_item.match_status = match_status
            scope_item.match_details = match_details
        scope.processed_count = db.query(PipelineRunScopeItem).filter(
            PipelineRunScopeItem.pipeline_run_scope_id == scope.id,
            PipelineRunScopeItem.status == "processed",
        ).count()

    triage_result = normalized.model_dump()
    if normalization_error:
        triage_result["error"] = normalization_error
        triage_result["quality"] = quality

    db.add(ListingAnalysis(
        id=f"analysis-{uuid.uuid4().hex[:20]}",
        listing_id=listing.id,
        pipeline_run_id=run.id,
        stage="triage",
        status=normalization_status,
        model_id=getattr(gateway, "model_extraction", "deterministic-local"),
        result=triage_result,
        photos_examined=0,
        external_navigation_count=detail.external_navigation_count,
        created_at=now,
    ))
    db.add(
        ListingSnapshot(
            listing_id=listing.id,
            pipeline_run_id=run.id,
            product_id=product.id,
            market_cohort_id=product.market_cohort_id,
            category=normalized.category,
            item_form=normalized.item_form,
            attributes=listing.attributes,
            classification_status=normalized.classification_status,
            classification_confidence=normalized.confidence,
            taxonomy_version=normalized.taxonomy_version,
            schema_version=normalized.schema_version,
            analytics_eligible=eligibility.analytics_eligible,
            exclusion_codes=eligibility.exclusion_codes,
            classification_evidence=normalized.classification_evidence,
            price=detail.price,
            status="active",
            observed_at=now,
            evidence_level="detail",
            listing_discovery_id=discovery.id,
        )
    )
    _record_navigation(db, run.id, "enrichment", task.id, task.started_at)
    db.commit()


def _save_parser_artifact(
    db,
    run: PipelineRun,
    task: ListingEnrichmentTask,
    exc: MarketplaceOperationError,
    attempt: PipelineWorkloadAttempt,
) -> None:
    discovery = task.discovery
    diag = exc.detail.get("diagnostics", {}) if isinstance(exc.detail, dict) else {}
    db.add(PipelineParserArtifact(
        id=f"artifact-{uuid.uuid4().hex[:20]}",
        pipeline_run_id=run.id,
        task_id=task.id,
        attempt_number=attempt.attempt_number if attempt else 1,
        parser_version=exc.parser_version or "olx-dom-2026-08-24",
        build_sha=os.getenv("BUILD_SHA", "dev"),
        stage=exc.stage or "detail",
        url_hash=hashlib.sha256(discovery.external_id.encode("utf-8")).hexdigest()[:16],
        page_state=diag.get("page_state", "detail_incomplete"),
        http_status=exc.http_status or 502,
        missing_fields=exc.missing_fields,
        selectors_tried=int(diag.get("selectors_tried", 5)),
        provenance=diag.get("provenance", {}),
        sanitized_snippet=diag.get("sanitized_snippet", ""),
        created_at=_now(),
    ))


def _next_pending_at(db, run_id: str) -> datetime | None:
    values = [
        row[0] for row in db.query(PipelineRetrievalTask.not_before).filter(
            PipelineRetrievalTask.pipeline_run_id == run_id,
            PipelineRetrievalTask.status == "pending",
            PipelineRetrievalTask.not_before.is_not(None),
        ).all()
    ] + [
        row[0] for row in db.query(ListingEnrichmentTask.not_before).filter(
            ListingEnrichmentTask.pipeline_run_id == run_id,
            ListingEnrichmentTask.status == "pending",
            ListingEnrichmentTask.not_before.is_not(None),
        ).all()
    ]
    return min(values) if values else None


async def run_high_volume_pipeline(
    run_id: str,
    source_type: str = "fixture",
    query: str = "",
    limit: int = 5,
    model_mode: str = "auto",
    investigate_limit: int = 2,
) -> dict[str, Any]:
    """Execute a safe slice of a high-volume workload."""
    del model_mode, investigate_limit
    worker_id = f"worker-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    db = SessionLocal()
    try:
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if not run:
            raise ValueError(f"Pipeline run {run_id} not found")
        configuration = _configuration(run)
        settings = _settings(configuration)
        live = _is_live_source(source_type)
        await _initialize(db, run, settings, source_type, query, limit)

        # Ensure reaper checks unexpired state
        reap_stale_tasks_and_workloads(db)

        attempt = _get_or_create_active_attempt(db, run, reason="initial")
        run.started_at = run.started_at or _now()
        run.workload_not_before = None

        if run.status != "running":
            _record_state_transition(db, run, "running", "worker_execution", attempt_number=attempt.attempt_number)
        db.commit()

        if run.workload_deadline and _now() >= run.workload_deadline:
            return _finalize(
                db,
                run,
                settings,
                reason="Prazo da execução atingido; dados coletados e tarefas pendentes foram preservados.",
                source_type=source_type,
            )

        source = get_marketplace_source(source_type)

        while True:
            run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
            assert run is not None
            if run.workload_deadline and _now() >= run.workload_deadline:
                return _finalize(
                    db,
                    run,
                    settings,
                    reason="Prazo da execução atingido; dados coletados e tarefas pendentes foram preservados.",
                    source_type=source_type,
                )
            state = _refresh_counts(db, run, settings, "discovery")
            consumed = int(state["navigations_consumed"])
            retrievals_consumed = db.query(PipelineNavigationLedger).filter(
                PipelineNavigationLedger.pipeline_run_id == run_id,
                PipelineNavigationLedger.task_kind == "retrieval",
                PipelineNavigationLedger.status == "completed",
            ).count()
            retrieval_cap = (
                settings["navigation_cap"]
                if state.get("detail_reserve_released")
                else settings["discovery_navigation_cap"]
            )

            if consumed >= settings["navigation_cap"]:
                return _finalize(
                    db,
                    run,
                    settings,
                    reason="Limite de navegações planejado para esta execução atingido.",
                    source_type=source_type,
                )

            retrieval = None
            if retrievals_consumed < retrieval_cap:
                retrieval = claim_next_retrieval_task(db, run_id, worker_id, lease_seconds=settings["lease_seconds"])
            if retrieval:
                try:
                    await _process_retrieval(db, run, retrieval, source, source_type, settings)
                except OlxAccessError as exc:
                    db.rollback()
                    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
                    retrieval = db.query(PipelineRetrievalTask).filter(PipelineRetrievalTask.id == retrieval.id).first()
                    if run and retrieval:
                        retrieval.status = "pending"
                        retrieval.lease_owner = None
                        retrieval.lease_expires_at = None
                        retrieval.error_code = exc.code
                        retrieval.error_message = str(exc.detail)
                        retry_at = _retry_at(exc)
                        retrieval.not_before = retry_at
                        _queue(
                            db,
                            run,
                            settings,
                            "discovery",
                            retry_at,
                            status="waiting_budget",
                            reason_code=exc.code,
                            message="Aguardando a disponibilidade OLX para retomar a mesma página.",
                        )
                        state = _refresh_counts(db, run, settings, "waiting_budget")
                        _set_step(run, "waiting_budget", state)
                        db.commit()
                        return {"status": run.status, **state}
                    raise
                except Exception as exc:
                    db.rollback()
                    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
                    retrieval = db.query(PipelineRetrievalTask).filter(PipelineRetrievalTask.id == retrieval.id).first()
                    if run and retrieval:
                        retrieval.status = "failed"
                        retrieval.lease_owner = None
                        retrieval.lease_expires_at = None
                        retrieval.error_code = type(exc).__name__
                        retrieval.error_message = str(exc)
                        retrieval.last_error_code = type(exc).__name__
                        retrieval.last_error_at = _now()
                        retrieval.finished_at = _now()
                        state = _refresh_counts(db, run, settings, "discovery")
                        _set_step(run, "partial", state)
                        db.commit()
                    if live:
                        raise

                if live:
                    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
                    when = _now() + timedelta(seconds=settings["pace_seconds"])
                    _queue(db, run, settings, "discovery", when, status="queued", reason_code="scheduled_pace")
                    state = _refresh_counts(db, run, settings, "discovery")
                    _set_step(run, "queued", state)
                    db.commit()
                    return {"status": run.status, **state}
                continue

            retrievals_remaining = db.query(PipelineRetrievalTask).filter(
                PipelineRetrievalTask.pipeline_run_id == run_id,
                PipelineRetrievalTask.status == "pending",
            ).count()
            if retrievals_remaining:
                if retrievals_consumed >= retrieval_cap:
                    db.query(PipelineRetrievalTask).filter(
                        PipelineRetrievalTask.pipeline_run_id == run_id,
                        PipelineRetrievalTask.status == "pending",
                    ).update({"status": "deferred"}, synchronize_session=False)
                    db.commit()
                    retrievals_remaining = 0
                else:
                    when = _next_pending_at(db, run_id) or (_now() + timedelta(seconds=settings["pace_seconds"]))
                    target_status = "waiting_budget" if live else "queued"
                    _queue(db, run, settings, "discovery", when, status=target_status, reason_code="awaiting_pending_retrieval")
                    state = _refresh_counts(db, run, settings, target_status)
                    _set_step(run, target_status, state)
                    db.commit()
                    return {"status": run.status, **state}

            if retrievals_remaining:
                when = _next_pending_at(db, run_id) or (_now() + timedelta(seconds=settings["pace_seconds"]))
                target_status = "waiting_budget" if live else "queued"
                _queue(db, run, settings, "discovery", when, status=target_status, reason_code="awaiting_pending_retrieval")
                state = _refresh_counts(db, run, settings, target_status)
                _set_step(run, target_status, state)
                db.commit()
                return {"status": run.status, **state}

            if prepare_card_review_stage(db, run, settings):
                state = _refresh_counts(db, run, settings, "reviewing_cards")
                return {"status": run.status, **state}

            _plan_enrichment(db, run, settings)
            state = _refresh_counts(db, run, settings, "enrichment")

            # Check if parser circuit is open
            if state.get("parser_circuit_open"):
                trip_reason = state.get("circuit_trip_reason", "Circuito de parser aberto por falhas consecutivas.")
                _queue(
                    db,
                    run,
                    settings,
                    "enrichment",
                    _now() + timedelta(hours=1),
                    status="waiting_worker",
                    reason_code="parser_circuit_open",
                    message=trip_reason,
                )
                db.commit()
                return {"status": run.status, **state}

            enrichment = claim_next_enrichment_task(db, run_id, worker_id, lease_seconds=settings["lease_seconds"])
            if enrichment:
                try:
                    await _process_enrichment(db, run, enrichment, source, source_type, attempt)
                    # Reset consecutive parser failures on success
                    state["parser_consecutive_failures"] = 0
                    history = list(state.get("parser_failure_history", []))
                    history.append(False)
                    if len(history) > settings["parser_failure_rate_window"]:
                        history.pop(0)
                    state["parser_failure_history"] = history
                    run.workload_state = state
                    db.commit()

                except MarketplaceOperationError as exc:
                    db.rollback()
                    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
                    enrichment = db.query(ListingEnrichmentTask).filter(ListingEnrichmentTask.id == enrichment.id).first()
                    if run and enrichment:
                        enrichment.lease_owner = None
                        enrichment.lease_expires_at = None
                        enrichment.last_error_code = exc.code
                        enrichment.last_error_at = _now()

                        if exc.code == "page_missing_required_fields" or exc.kind == "parser":
                            _save_parser_artifact(db, run, enrichment, exc, attempt)
                            enrichment.status = "skipped_unverified"
                            enrichment.finished_at = _now()
                            enrichment.error_code = exc.code
                            enrichment.error_message = exc.message
                            if enrichment.discovery:
                                enrichment.discovery.triage_status = "unverified"
                                enrichment.discovery.triage_reason = f"Extração incompleta: {', '.join(exc.missing_fields)}"

                            # Update parser circuit breaker counters
                            consecutive = state.get("parser_consecutive_failures", 0) + 1
                            history = list(state.get("parser_failure_history", []))
                            history.append(True)
                            if len(history) > settings["parser_failure_rate_window"]:
                                history.pop(0)
                            state["parser_consecutive_failures"] = consecutive
                            state["parser_failure_history"] = history
                            rate = sum(1 for x in history if x) / len(history) if history else 0.0

                            if (
                                consecutive >= settings["parser_consecutive_failure_limit"]
                                or (len(history) >= 10 and rate > settings["parser_failure_rate_limit"])
                            ):
                                state["parser_circuit_open"] = True
                                trip_msg = f"Circuito de parser aberto: {consecutive} falhas consecutivas ({rate:.0%} erro)."
                                state["circuit_trip_reason"] = trip_msg
                                _queue(
                                    db,
                                    run,
                                    settings,
                                    "enrichment",
                                    _now() + timedelta(hours=1),
                                    status="waiting_worker",
                                    reason_code="parser_circuit_open",
                                    message=trip_msg,
                                )
                                db.commit()
                                return {"status": run.status, **state}

                            # Circuit NOT open: advance to next detail smoothly!
                            db.commit()

                        elif exc.retry_policy in {"waiting_budget", "blocked", "waiting_session"}:
                            retry_at = _retry_at(exc) if isinstance(exc, OlxAccessError) else (_now() + timedelta(minutes=5))
                            enrichment.status = "pending"
                            enrichment.not_before = retry_at
                            _queue(
                                db,
                                run,
                                settings,
                                "enrichment",
                                retry_at,
                                status=exc.retry_policy,
                                reason_code=exc.code,
                                message=exc.message,
                            )
                            state = _refresh_counts(db, run, settings, exc.retry_policy)
                            _set_step(run, exc.retry_policy, state)
                            db.commit()
                            return {"status": run.status, **state}
                        else:
                            # General failure
                            enrichment.status = "failed"
                            enrichment.error_code = exc.code
                            enrichment.error_message = exc.message
                            enrichment.finished_at = _now()
                            db.commit()

                except OlxAccessError as exc:
                    db.rollback()
                    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
                    enrichment = db.query(ListingEnrichmentTask).filter(ListingEnrichmentTask.id == enrichment.id).first()
                    if run and enrichment:
                        enrichment.status = "pending"
                        enrichment.lease_owner = None
                        enrichment.lease_expires_at = None
                        enrichment.error_code = exc.code
                        enrichment.error_message = str(exc.detail)
                        retry_at = _retry_at(exc)
                        enrichment.not_before = retry_at
                        _queue(
                            db,
                            run,
                            settings,
                            "enrichment",
                            retry_at,
                            status="waiting_budget",
                            reason_code=exc.code,
                            message="Aguardando a disponibilidade OLX para retomar o anúncio pendente.",
                        )
                        state = _refresh_counts(db, run, settings, "waiting_budget")
                        _set_step(run, "waiting_budget", state)
                        db.commit()
                        return {"status": run.status, **state}
                    raise

                except Exception as exc:
                    db.rollback()
                    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
                    enrichment = db.query(ListingEnrichmentTask).filter(ListingEnrichmentTask.id == enrichment.id).first()
                    if run and enrichment:
                        enrichment.status = "failed"
                        enrichment.lease_owner = None
                        enrichment.lease_expires_at = None
                        enrichment.error_code = type(exc).__name__
                        enrichment.error_message = str(exc)
                        enrichment.last_error_code = type(exc).__name__
                        enrichment.last_error_at = _now()
                        enrichment.finished_at = _now()
                        db.commit()

                if live:
                    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
                    when = _now() + timedelta(seconds=settings["pace_seconds"])
                    _queue(db, run, settings, "enrichment", when, status="queued", reason_code="scheduled_pace")
                    state = _refresh_counts(db, run, settings, "enrichment")
                    _set_step(run, "queued", state)
                    db.commit()
                    return {"status": run.status, **state}
                continue

            remaining = db.query(ListingEnrichmentTask).filter(
                ListingEnrichmentTask.pipeline_run_id == run_id,
                ListingEnrichmentTask.status == "pending",
            ).count()
            if remaining:
                when = _next_pending_at(db, run_id) or (_now() + timedelta(seconds=settings["pace_seconds"]))
                target_status = "waiting_budget" if live else "queued"
                _queue(db, run, settings, "enrichment", when, status=target_status, reason_code="awaiting_pending_enrichment")
                state = _refresh_counts(db, run, settings, target_status)
                _set_step(run, target_status, state)
                db.commit()
                return {"status": run.status, **state}

            deferred_retrievals = db.query(PipelineRetrievalTask).filter(
                PipelineRetrievalTask.pipeline_run_id == run_id,
                PipelineRetrievalTask.status == "deferred",
            ).count()
            if deferred_retrievals and int(state["navigations_consumed"]) < settings["navigation_cap"]:
                db.query(PipelineRetrievalTask).filter(
                    PipelineRetrievalTask.pipeline_run_id == run_id,
                    PipelineRetrievalTask.status == "deferred",
                ).update({"status": "pending"}, synchronize_session=False)
                state["detail_reserve_released"] = True
                run.workload_state = state
                db.commit()
                continue

            run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
            assert run is not None
            return _finalize(
                db,
                run,
                settings,
                reason="Execução concluída; todos os detalhes planejados foram processados.",
                source_type=source_type,
            )
    finally:
        db.close()


def cancel_high_volume_run(run_id: str, actor: str = "operator") -> dict[str, Any]:
    db = SessionLocal()
    try:
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if not run:
            raise ValueError(f"Pipeline run {run_id} not found")
        if run.status in {"completed", "completed_partial", "failed", "blocked", "cancelled"}:
            return {"id": run.id, "status": run.status, "message": "Pipeline já finalizado."}

        db.query(PipelineRetrievalTask).filter(
            PipelineRetrievalTask.pipeline_run_id == run_id,
            PipelineRetrievalTask.status.in_(("pending", "running", "deferred", "waiting_budget")),
        ).update({"status": "cancelled"}, synchronize_session=False)

        db.query(ListingEnrichmentTask).filter(
            ListingEnrichmentTask.pipeline_run_id == run_id,
            ListingEnrichmentTask.status.in_(("pending", "running", "deferred", "waiting_budget")),
        ).update({"status": "cancelled"}, synchronize_session=False)

        active_attempt = (
            db.query(PipelineWorkloadAttempt)
            .filter(
                PipelineWorkloadAttempt.pipeline_run_id == run.id,
                PipelineWorkloadAttempt.status.in_(("running", "queued", "waiting_budget", "waiting_worker")),
            )
            .order_by(PipelineWorkloadAttempt.attempt_number.desc())
            .first()
        )
        if active_attempt:
            active_attempt.status = "cancelled"
            active_attempt.finished_at = _now()

        state = dict(run.workload_state or {})
        state["phase"] = "cancelled"
        state["message"] = "Execução cancelada pelo operador."
        run.workload_state = state
        run.finished_at = _now()
        if run.started_at and run.finished_at:
            run.duration_seconds = max(0.0, (run.finished_at - run.started_at).total_seconds())

        _record_state_transition(
            db,
            run,
            to_status="cancelled",
            reason_code="operator_cancel",
            attempt_number=active_attempt.attempt_number if active_attempt else 1,
            actor=actor,
            message="Cancelado pelo operador.",
        )
        db.commit()
        db.refresh(run)
        return {"id": run.id, "status": run.status, "message": "Pipeline cancelado pelo operador."}
    finally:
        db.close()
