"""Durable agent processing for search-card-only discovery evidence.

This queue never navigates to a marketplace URL. Its output can refine the
card-derived Listing projection, but remains constrained to title and fields
present in the search card.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from typing import Optional

from sqlalchemy import or_, select

from packages.ai.metrics import model_metric_context
from packages.ai.model_gateway import AgentCardGateResult, DeterministicLocalModelGateway, get_model_gateway
from packages.core.database import SessionLocal
from packages.core.models import ListingDiscovery, ListingEnrichmentTask, OpportunitySignal, PipelineRun
from packages.pipeline.card_gate import (
    CARD_GATE_CONTRACT_VERSION,
    apply_cheap_gate,
    card_evidence_hash,
)


TERMINAL_PIPELINE_STATUSES = {"completed", "completed_partial", "failed", "blocked"}
REVIEW_PIPELINE_STATUSES = TERMINAL_PIPELINE_STATUSES | {"reviewing_cards"}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _clean_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<\s*br\s*/?\s*>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _incomplete_cards_query(db, *, profile_id: Optional[str] = None):
    query = (
        db.query(ListingDiscovery)
        .join(PipelineRun, PipelineRun.id == ListingDiscovery.pipeline_run_id)
        .outerjoin(ListingEnrichmentTask, ListingEnrichmentTask.listing_discovery_id == ListingDiscovery.id)
        .filter(PipelineRun.workload_mode == "high_volume")
        .filter(or_(ListingEnrichmentTask.id.is_(None), ListingEnrichmentTask.status != "completed"))
    )
    if profile_id:
        query = query.filter(PipelineRun.profile_id == profile_id)
    return query


def latest_incomplete_run_id(db, profile_id: str) -> Optional[str]:
    row = (
        _incomplete_cards_query(db, profile_id=profile_id)
        .with_entities(PipelineRun.id)
        .filter(PipelineRun.status.in_(TERMINAL_PIPELINE_STATUSES))
        .order_by(PipelineRun.started_at.desc())
        .first()
    )
    return str(row[0]) if row else None


def queue_latest_incomplete_cards(profile_id: str) -> dict[str, int | str | None]:
    """Queue the latest terminal high-volume run for card-only analysis."""
    db = SessionLocal()
    try:
        run_id = latest_incomplete_run_id(db, profile_id)
        if not run_id:
            return {"run_id": None, "queued": 0, "total": 0}
        query = _incomplete_cards_query(db, profile_id=profile_id).filter(ListingDiscovery.pipeline_run_id == run_id)
        total = query.count()
        queued = _queue_eligible_cards(query.all())
        db.commit()
        return {"run_id": run_id, "queued": int(queued), "total": int(total)}
    finally:
        db.close()


def queue_run_cards(run_id: str) -> dict[str, int | str]:
    """Queue every still-incomplete card in one run without marketplace I/O."""
    db = SessionLocal()
    try:
        query = _incomplete_cards_query(db).filter(ListingDiscovery.pipeline_run_id == run_id)
        total = query.count()
        queued = _queue_eligible_cards(query.all())
        db.commit()
        return {"run_id": run_id, "queued": int(queued), "total": int(total)}
    finally:
        db.close()


def retry_failed_run_cards(run_id: str) -> dict[str, int | str]:
    """Explicitly retry failed required reviews; no paid retry is automatic."""
    db = SessionLocal()
    try:
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if run is None or run.workload_mode != "high_volume":
            raise ValueError("High-volume pipeline not found")
        failed = db.query(ListingDiscovery).filter(
            ListingDiscovery.pipeline_run_id == run_id,
            ListingDiscovery.card_analysis_status == "failed",
        ).all()
        queued = _queue_eligible_cards(failed, include_failed=True)
        if queued:
            run.status = "reviewing_cards"
            state = dict(run.workload_state or {})
            state["phase"] = "reviewing_cards"
            run.workload_state = state
        db.commit()
        return {"run_id": run_id, "queued": int(queued)}
    finally:
        db.close()


def _queue_eligible_cards(cards: list[ListingDiscovery], *, include_failed: bool = False) -> int:
    """Create paid work only after the full cheap gate has approved it."""
    queued = 0
    for discovery in cards:
        decision = apply_cheap_gate(discovery)
        if not decision.approved:
            discovery.card_analysis_status = "not_needed"
            discovery.card_analysis_error = decision.reason_code
            discovery.card_analysis_updated_at = _now()
            continue
        reusable = (
            discovery.card_analysis_status == "completed"
            and discovery.card_analysis_contract_version == CARD_GATE_CONTRACT_VERSION
            and discovery.card_analysis_evidence_hash == card_evidence_hash(discovery)
        )
        if reusable:
            continue
        # A completed decision is reused only with the same contract and card
        # evidence.  Older generic-normalizer output is deliberately queued
        # again when an operator turns on required review.
        allowed = {"not_requested", "not_needed", "completed", "fallback"}
        if include_failed:
            allowed.add("failed")
        if discovery.card_analysis_status not in allowed:
            continue
        discovery.card_analysis_status = "pending"
        discovery.card_analysis_error = None
        discovery.card_analysis_updated_at = _now()
        queued += 1
    return queued


def reap_stale_card_analyses(db, *, stale_minutes: int = 15) -> int:
    """Turn orphaned model calls into explicit failures without paid retries."""
    cutoff = _now() - dt.timedelta(minutes=max(1, stale_minutes))
    stale = db.query(ListingDiscovery).filter(
        ListingDiscovery.card_analysis_status == "running",
        ListingDiscovery.card_analysis_updated_at < cutoff,
    ).all()
    affected_runs: set[str] = set()
    for discovery in stale:
        discovery.card_analysis_status = "failed"
        discovery.card_analysis_error = "agent_review_stale"
        discovery.card_analysis_updated_at = _now()
        affected_runs.add(discovery.pipeline_run_id)
    db.flush()
    if stale:
        from packages.pipeline.high_volume import complete_card_review_stage
        for run_id in affected_runs:
            run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
            if run:
                complete_card_review_stage(db, run)
    return len(stale)


def claim_next_card_analysis() -> Optional[str]:
    """Claim one card, keeping processing low-rate and restart-safe."""
    db = SessionLocal()
    try:
        query = (
            _incomplete_cards_query(db)
            .filter(ListingDiscovery.card_analysis_status == "pending")
            .filter(PipelineRun.status.in_(REVIEW_PIPELINE_STATUSES))
            .order_by(ListingDiscovery.priority_score.desc(), ListingDiscovery.first_observed_at.asc())
        )
        if db.bind and db.bind.dialect.name == "postgresql":
            query = query.with_for_update(of=ListingDiscovery, skip_locked=True)
        discovery = query.first()
        if discovery is None:
            return None
        discovery.card_analysis_status = "running"
        discovery.card_analysis_updated_at = _now()
        db.commit()
        return discovery.id
    finally:
        db.close()


def _category_hint(discovery: ListingDiscovery) -> Optional[str]:
    scope = discovery.scope_run
    snapshot = dict(scope.scope_snapshot or {}) if scope else {}
    return snapshot.get("category") or None


async def process_card_analysis(discovery_id: str) -> None:
    """Analyze one claimed card, failing closed for the optional run gate."""
    db = SessionLocal()
    try:
        discovery = db.query(ListingDiscovery).filter(ListingDiscovery.id == discovery_id).first()
        if discovery is None or discovery.card_analysis_status != "running":
            return
        decision = apply_cheap_gate(discovery)
        if not decision.approved:
            discovery.card_analysis_status = "not_needed"
            discovery.card_analysis_error = decision.reason_code
            discovery.card_analysis_updated_at = _now()
            db.flush()
            from packages.pipeline.high_volume import complete_card_review_stage
            complete_card_review_stage(db, discovery.pipeline_run)
            db.commit()
            return
        config = ((discovery.pipeline_run.steps or [{}])[0]).get("configuration", {})
        required = (config.get("high_volume") or {}).get("card_review_mode") == "required"
        if not required:
            # Disabled mode is deliberately cost-free.  It never falls through
            # to a local or remote model implementation.
            discovery.card_analysis_status = "not_needed"
            discovery.card_analysis_updated_at = _now()
            db.commit()
            return
        title = _clean_text(discovery.title)
        scope = discovery.scope_run
        snapshot = dict(scope.scope_snapshot or {}) if scope else {}
        target_category = snapshot.get("category") or "hardware"
        expected_item_form = snapshot.get("item_form") or "standalone"
        context = {
            "evidence_scope": "search_card_only",
            "card_price": discovery.price,
            "card_price_origin": discovery.price_origin,
            "card_location": discovery.location or None,
            "card_condition": discovery.condition or None,
            "card_seller": discovery.seller or None,
            "target_category": target_category,
            "expected_item_form": expected_item_form,
            "evidence_limit": "Não há página de detalhe. Campos ausentes devem permanecer UNKNOWN ou não verificados.",
        }
        gateway = get_model_gateway()
        model_id = str(getattr(gateway, "model_extraction", "deterministic-local"))
        try:
            if required and isinstance(gateway, DeterministicLocalModelGateway):
                raise RuntimeError("agent_provider_unavailable")
            # This counter represents actual paid/provider attempts, not a
            # worker claim.  Cheap-gate rechecks therefore stay at zero.
            discovery.card_analysis_attempts += 1
            with model_metric_context(
                pipeline_run_id=discovery.pipeline_run_id,
                origin="discovery_card_analysis",
                operation="card_gate",
            ):
                result = await gateway.gate_card(title, card_context=context)
            if not isinstance(result, AgentCardGateResult):
                result = AgentCardGateResult.model_validate(result)
            discovery.card_analysis_status = "completed"
            discovery.card_analysis_error = None
        except Exception as exc:
            is_contract_error = type(exc).__name__ in {"ValidationError", "ValueError", "TypeError"}
            reason_code = "AGENT_INVALID_RESPONSE" if is_contract_error else "AGENT_PROVIDER_ERROR"
            discovery.card_analysis_error = reason_code
            if required:
                discovery.card_analysis_status = "failed"
                discovery.card_analysis_result = None
                discovery.card_analysis_model_id = model_id
                discovery.card_analysis_contract_version = CARD_GATE_CONTRACT_VERSION
                discovery.card_analysis_evidence_hash = card_evidence_hash(discovery)
                discovery.card_analysis_updated_at = _now()
                db.flush()
                from packages.pipeline.high_volume import complete_card_review_stage
                complete_card_review_stage(db, discovery.pipeline_run)
                db.commit()
                return
            discovery.card_analysis_status = "not_needed"
            discovery.card_analysis_updated_at = _now()
            db.commit()
            return
        discovery.card_analysis_result = result.model_dump(mode="json")
        discovery.card_analysis_model_id = model_id
        discovery.card_analysis_contract_version = CARD_GATE_CONTRACT_VERSION
        discovery.card_analysis_evidence_hash = card_evidence_hash(discovery)
        discovery.card_analysis_updated_at = _now()
        if required:
            db.flush()
            from packages.pipeline.high_volume import complete_card_review_stage
            complete_card_review_stage(db, discovery.pipeline_run)
            db.commit()
            return
        # The agent refines semantics but still has card-only evidence.  Re-run
        # the projection rather than treating its result as a detailed listing.
        from packages.pipeline.card_projection import project_discovery, refresh_preliminary_signals
        project_discovery(db, discovery, override=discovery.card_analysis_result)
        from packages.pipeline.high_volume import _run_market_consolidation
        _run_market_consolidation(db, discovery.pipeline_run, "olx" if "olx" in (discovery.normalized_url or "").casefold() else "fixture")
        refresh_preliminary_signals(db, discovery.pipeline_run_id)
        db.commit()
        active = _incomplete_cards_query(db).filter(
            ListingDiscovery.pipeline_run_id == discovery.pipeline_run_id,
            ListingDiscovery.card_analysis_status.in_(("pending", "running")),
        ).count()
        high_volume = config.get("high_volume") or {}
        if active == 0 and high_volume.get("detail_access_mode") == "auto_threshold":
            from packages.pipeline.high_volume import schedule_signal_enrichment

            schedule_signal_enrichment(
                discovery.pipeline_run_id,
                threshold=float(high_volume.get("enrichment_threshold", 70)),
                max_items=int(high_volume.get("enrichment_cap", 100)),
                actor="scheduler",
            )
    except Exception:
        db.rollback()
        discovery = db.query(ListingDiscovery).filter(ListingDiscovery.id == discovery_id).first()
        if discovery and discovery.card_analysis_status == "running":
            discovery.card_analysis_status = "failed"
            discovery.card_analysis_error = "card_analysis_persistence_failed"
            discovery.card_analysis_updated_at = _now()
            db.commit()
        raise
    finally:
        db.close()


def card_analysis_counts(db, profile_id: str, run_id: str) -> dict[str, int]:
    query = _incomplete_cards_query(db, profile_id=profile_id).filter(ListingDiscovery.pipeline_run_id == run_id)
    return {
        "total": query.count(),
        "not_requested": query.filter(ListingDiscovery.card_analysis_status == "not_requested").count(),
        "pending": query.filter(ListingDiscovery.card_analysis_status == "pending").count(),
        "running": query.filter(ListingDiscovery.card_analysis_status == "running").count(),
        "completed": query.filter(ListingDiscovery.card_analysis_status == "completed").count(),
        "fallback": query.filter(ListingDiscovery.card_analysis_status == "fallback").count(),
        "failed": query.filter(ListingDiscovery.card_analysis_status == "failed").count(),
    }


def run_immediate_card_triage(db, run_id: str) -> int:
    """Compatibility entrypoint for deterministic card projection."""
    from packages.pipeline.card_projection import project_run_cards
    result = project_run_cards(db, run_id)
    db.commit()
    return int(result["published"])
