"""Promote search-card evidence without requiring marketplace navigation.

The raw ``ListingDiscovery`` remains the source evidence.  This module builds
an idempotent, explicitly unaudited read model for Listings, preliminary
market statistics and opportunity signals.  It never calls a marketplace.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func

from packages.classification.classifier import classify_listing_text
from packages.core.hardware_taxonomy import (
    ClassificationStatus,
    ItemForm,
    HARDWARE_SCHEMA_VERSION,
    HARDWARE_TAXONOMY_VERSION,
)
from packages.core.models import (
    Listing,
    ListingDiscovery,
    ListingSnapshot,
    MarketSnapshot,
    OpportunitySignal,
    Opportunity,
    PipelineRun,
    Product,
)
from packages.search.matching import evaluate_candidate
from packages.search.models import SearchPlanV1
from packages.pipeline.card_gate import apply_cheap_gate, is_direct_price_valid
from packages.ai.model_gateway import AgentCardGateResult
from packages.core.urls import canonical_source_url

RULE_VERSION = "card-projection-v1"
MIN_MARKET_SAMPLE = 5

CARD_TERMINAL_STATUSES = {"completed", "fallback", "failed", "not_needed"}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fold(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def _canonical_identity(category: str, brand: str, model: str, variant: str | None) -> tuple[str, str, str, str | None]:
    """Normalize high-frequency marketplace aliases before product resolution."""
    brand = (brand or "Generic").strip() or "Generic"
    model = re.sub(r"\s+", " ", (model or "").strip())
    folded = _fold(model)
    if category in ("other", "console"):
        aliases = {
            "ps5": ("Sony", "PlayStation 5"),
            "playstation 5": ("Sony", "PlayStation 5"),
            "ps 5": ("Sony", "PlayStation 5"),
            "ps5 slim": ("Sony", "PlayStation 5"),
            "playstation 5 slim": ("Sony", "PlayStation 5"),
            "ps5 digital": ("Sony", "PlayStation 5"),
            "playstation 5 digital": ("Sony", "PlayStation 5"),
            "ps5 pro": ("Sony", "PlayStation 5 Pro"),
            "playstation 5 pro": ("Sony", "PlayStation 5 Pro"),
            "ps4": ("Sony", "PlayStation 4"),
            "playstation 4": ("Sony", "PlayStation 4"),
            "ps4 pro": ("Sony", "PlayStation 4 Pro"),
            "ps4 slim": ("Sony", "PlayStation 4"),
            "xbox series x": ("Microsoft", "Xbox Series X"),
            "xbox series s": ("Microsoft", "Xbox Series S"),
            "xbox one": ("Microsoft", "Xbox One"),
            "xbox one s": ("Microsoft", "Xbox One S"),
            "xbox one x": ("Microsoft", "Xbox One X"),
            "nintendo switch": ("Nintendo", "Nintendo Switch"),
            "nintendo switch oled": ("Nintendo", "Nintendo Switch OLED"),
            "nintendo switch lite": ("Nintendo", "Nintendo Switch Lite"),
            "steam deck": ("Valve", "Steam Deck"),
            "rog ally": ("ASUS", "ROG Ally"),
        }
        if folded in aliases:
            brand, model = aliases[folded]
    return category, brand, model or "Não identificado", variant


def _rule_payload(discovery: ListingDiscovery) -> dict[str, Any]:
    result = classify_listing_text(title=discovery.title or "", description="")
    category, brand, model, variant = _canonical_identity(
        str(result.category.value if hasattr(result.category, "value") else result.category),
        result.brand,
        result.model,
        result.variant,
    )
    return {
        "category": category,
        "item_form": str(result.item_form.value if hasattr(result.item_form, "value") else result.item_form),
        "classification_status": str(result.status.value if hasattr(result.status, "value") else result.status),
        "confidence": result.confidence,
        "brand": brand,
        "model": model,
        "variant": variant,
        "extracted_attributes": result.attributes or {},
        "classification_evidence": result.evidence or [],
        "listing_summary": result.listing_summary,
    }


def _effective_payload(discovery: ListingDiscovery) -> dict[str, Any]:
    """Prefer a useful card-agent result without allowing it to weaken a rule match.

    The agent is optional and sees exactly the same bounded evidence as the
    rule classifier.  It may clarify a weak result, but a low-confidence
    ``unverified`` response must not erase a strong deterministic match.
    """
    rule = dict(discovery.rule_analysis_result or _rule_payload(discovery))
    agent = dict(discovery.card_analysis_result or {})
    if discovery.card_analysis_status != "completed" or not agent:
        return rule

    if agent.get("schema_version") == "agent-card-gate-v1":
        target = dict(agent.get("target_product") or {})
        if agent.get("verdict") != "target_product":
            return rule
        return {
            **rule,
            "brand": target.get("brand") or rule.get("brand"),
            "model": target.get("model") or rule.get("model"),
            "variant": target.get("variant"),
            "confidence": agent.get("confidence"),
            "classification_status": ClassificationStatus.CONFIRMED.value,
            "item_form": rule.get("item_form") if rule.get("item_form") not in {ItemForm.UNKNOWN.value, ItemForm.ACCESSORY.value, ItemForm.PARTS.value} else ItemForm.STANDALONE.value,
            "classification_evidence": list(agent.get("evidence") or []),
        }

    merged = {**rule, **{key: value for key, value in agent.items() if value not in (None, "", [], {})}}
    rule_confidence = float(rule.get("confidence") or 0)
    agent_confidence = float(agent.get("confidence") or 0)
    if (
        rule.get("classification_status") == ClassificationStatus.CONFIRMED.value
        and agent.get("classification_status") != ClassificationStatus.CONFIRMED.value
        and agent_confidence <= rule_confidence
    ):
        for key in ("category", "item_form", "classification_status", "confidence", "brand", "model", "variant"):
            merged[key] = rule.get(key)
    return merged


def _scope_plan(discovery: ListingDiscovery) -> SearchPlanV1 | None:
    scope = discovery.scope_run
    payload = dict(scope.scope_snapshot or {}) if scope else {}
    plan = payload.get("plan")
    if not isinstance(plan, dict) or not plan.get("schema_version"):
        return None
    try:
        return SearchPlanV1.model_validate(plan)
    except (TypeError, ValueError):
        return None


def _candidate_from_payload(discovery: ListingDiscovery, payload: dict[str, Any]) -> dict[str, Any]:
    # A price inferred from arbitrary title text remains a signal. Only the
    # marketplace card's structured/DOM price may confirm a hard budget.
    price = discovery.price if discovery.price_origin in {"structured", "dom"} and discovery.price and discovery.price > 0 else None
    return {
        "source": _source_for(discovery),
        "external_id": discovery.external_id,
        "title": discovery.title,
        "price": price,
        "location": discovery.location or None,
        "condition": discovery.condition or None,
        "url": canonical_source_url(discovery.normalized_url or discovery.external_id),
        "category": payload.get("category"),
        "brand": payload.get("brand"),
        "model": payload.get("model"),
        "family": payload.get("family"),
        "generation": payload.get("generation"),
        "attributes": payload.get("extracted_attributes") or {},
    }


def _scope_item(discovery: ListingDiscovery):
    scope = discovery.scope_run
    if not scope:
        return None
    return next((item for item in scope.items if item.external_id == discovery.external_id), None)


def _record_match(discovery: ListingDiscovery, status: str, details: dict[str, Any]) -> None:
    item = _scope_item(discovery)
    if item is not None:
        if item.status == "processed" and item.match_status in {"confirmed", "collected"}:
            return
        item.match_status = status
        item.match_details = details
        if status == "confirmed":
            item.status = "agent_confirmed" if discovery.card_analysis_status == "completed" else "rule_confirmed"
            item.error_message = None
        elif status == "rejected":
            item.status = "rejected_agent" if discovery.card_analysis_status == "completed" else "rejected_summary"
            item.error_message = details.get("reason") or "Resultado fora do escopo salvo."
        else:
            item.status = "withheld_unverified"
            item.error_message = details.get("reason") or "Requisito obrigatório sem evidência suficiente."


def evaluate_discovery_gate(discovery: ListingDiscovery, *, require_agent: bool) -> tuple[str, dict[str, Any]]:
    """Return a publication decision bounded by card evidence and the saved plan."""
    cheap = apply_cheap_gate(discovery)
    if not cheap.approved:
        return "rejected", {
            "reason": cheap.reason_code or "Card rejeitado pelo gate determinístico.",
            "reason_code": cheap.reason_code,
            "publication_status": cheap.status,
        }
    if _is_rejected(discovery):
        return "rejected", {"reason": discovery.triage_reason or "Resultado rejeitado pela triagem determinística."}
    rule = dict(discovery.rule_analysis_result or _rule_payload(discovery))
    discovery.rule_analysis_status = "completed"
    discovery.rule_analysis_result = rule
    discovery.rule_version = RULE_VERSION
    plan = _scope_plan(discovery)

    def evaluate(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if payload.get("classification_status") != ClassificationStatus.CONFIRMED.value:
            return "unverified", {"reason": "Identidade do produto não confirmada pela evidência do card."}
        if float(payload.get("confidence") or 0) < 0.70:
            return "unverified", {"reason": "Confiança insuficiente para publicar o card."}
        if payload.get("item_form") in {ItemForm.ACCESSORY.value, ItemForm.PARTS.value, ItemForm.UNKNOWN.value}:
            return "rejected", {"reason": "O card representa acessório, peça ou formato não confirmado."}
        if plan is None:
            return "confirmed", {"reason": "Classificação determinística confirmada sem requisitos adicionais."}
        match = evaluate_candidate(_candidate_from_payload(discovery, payload), plan)
        details = match.model_dump()
        details["reason"] = (match.reasons or ["Escopo confirmado."])[0]
        return match.status, details

    rule_status, rule_details = evaluate(rule)
    if not require_agent:
        return rule_status, {"rule": rule_details, "reason": rule_details.get("reason")}

    if discovery.card_analysis_status != "completed" or not discovery.card_analysis_result:
        reason_code = discovery.card_analysis_error or (
            "AGENT_PROVIDER_ERROR" if discovery.card_analysis_status == "failed" else "AGENT_UNCERTAIN"
        )
        return "unverified", {"rule": rule_details, "agent": {"status": discovery.card_analysis_status}, "reason": reason_code, "reason_code": reason_code}

    try:
        agent = AgentCardGateResult.model_validate(discovery.card_analysis_result)
    except Exception:
        return "unverified", {"rule": rule_details, "reason": "AGENT_INVALID_RESPONSE", "reason_code": "AGENT_INVALID_RESPONSE"}
    if agent.confidence < 0.75:
        return "unverified", {"rule": rule_details, "reason": "AGENT_LOW_CONFIDENCE", "reason_code": "AGENT_LOW_CONFIDENCE"}
    if agent.verdict == "uncertain":
        return "unverified", {"rule": rule_details, "reason": "AGENT_UNCERTAIN", "reason_code": "AGENT_UNCERTAIN"}
    if agent.verdict == "exclude":
        return "rejected", {"rule": rule_details, "reason": agent.reason_code or "Excluído pelo agente.", "reason_code": agent.reason_code or "AGENT_EXCLUDE", "publication_status": "agent_rejected"}

    agent_payload = _effective_payload(discovery)
    agent_status, agent_details = evaluate(agent_payload)
    if agent_status != "confirmed":
        reason = "SCOPE_MISMATCH" if agent_status == "rejected" else "AGENT_UNCERTAIN"
        return ("rejected" if agent_status == "rejected" else "unverified"), {
            "rule": rule_details, "agent": agent_details, "reason": reason, "reason_code": reason,
            "publication_status": "rejected_scope" if agent_status == "rejected" else "withheld_unverified",
        }
    return "confirmed", {"rule": rule_details, "agent": agent_details, "reason": "Agente e escopo confirmados."}


def _withhold_existing_projection(db, discovery: ListingDiscovery, reason: str) -> None:
    snapshots = db.query(ListingSnapshot).filter(
        ListingSnapshot.listing_discovery_id == discovery.id,
    ).all()
    for snapshot in snapshots:
        snapshot.status = "excluded"
        snapshot.analytics_eligible = False
        snapshot.exclusion_codes = [reason or "UNVERIFIED_EVIDENCE"]
    listing = discovery.listing
    if listing:
        other_active = db.query(ListingSnapshot.id).filter(
            ListingSnapshot.listing_id == listing.id,
            ListingSnapshot.status == "active",
            ListingSnapshot.listing_discovery_id != discovery.id,
        ).first()
        if not other_active:
            listing.status = "inactive"
            listing.analytics_eligible = False
            listing.exclusion_codes = [reason or "UNVERIFIED_EVIDENCE"]
    _withdraw_signal(db, discovery, reason)


def _is_rejected(discovery: ListingDiscovery) -> bool:
    return (
        discovery.triage_status in {"rejected_summary", "duplicate"}
        and not str(discovery.publication_status or "").startswith("rejected_")
    )


def _is_ambiguous(payload: dict[str, Any], discovery: ListingDiscovery) -> bool:
    text = _fold(discovery.title)
    return (
        payload.get("classification_status") != ClassificationStatus.CONFIRMED.value
        or float(payload.get("confidence") or 0) < 0.70
        or payload.get("brand") == "Generic"
        or payload.get("model") in {"Não identificado", (discovery.title or "")[:40].strip()}
        or payload.get("item_form") == ItemForm.UNKNOWN.value
        or any(token in text for token in ("troca", "defeito", "conserto", "somente", "peca", "peça"))
    )


def _publishable(discovery: ListingDiscovery) -> tuple[bool, str]:
    if not is_direct_price_valid(discovery):
        return False, "NO_DIRECT_PRICE"
    if _is_rejected(discovery):
        return False, "Resultado rejeitado pela triagem de escopo."
    if not (discovery.title or "").strip():
        return False, "Card sem título utilizável."
    if not (discovery.normalized_url or discovery.external_id):
        return False, "Card sem fonte navegável."
    return True, "Card de busca publicado sem auditoria de detalhe."


def _market_eligible(payload: dict[str, Any], discovery: ListingDiscovery) -> bool:
    if not is_direct_price_valid(discovery):
        return False
    if payload.get("classification_status") != ClassificationStatus.CONFIRMED.value:
        return False
    if float(payload.get("confidence") or 0) < 0.70:
        return False
    if payload.get("item_form") in {ItemForm.ACCESSORY.value, ItemForm.PARTS.value, ItemForm.UNKNOWN.value}:
        return False
    return bool(payload.get("brand")) and bool(payload.get("model"))


def _resolve_product(db, payload: dict[str, Any]) -> Product:
    category, brand, model, variant = _canonical_identity(
        str(payload["category"]), str(payload.get("brand") or "Generic"), str(payload.get("model") or "Não identificado"), payload.get("variant")
    )
    key = "|".join(_fold(value) for value in (category, brand, model, variant or ""))
    candidates = db.query(Product).filter(Product.category == category).all()
    for candidate in candidates:
        candidate_key = "|".join(_fold(value) for value in (
            candidate.category, candidate.brand, candidate.model, candidate.variant or "",
        ))
        if candidate_key == key:
            return candidate
    product = Product(
        id=_stable_id("prod-card", key),
        category=category,
        brand=brand,
        family=model,
        model=model,
        variant=variant,
        display_name=" ".join(part for part in (brand, model, variant) if part),
        attributes=payload.get("extracted_attributes") or {},
        canonical_tier=1,
    )
    db.add(product)
    db.flush()
    return product


def project_discovery(db, discovery: ListingDiscovery, *, override: dict[str, Any] | None = None) -> Listing | None:
    """Apply deterministic or agent card classification to one discovery."""
    # Projection is deliberately a second enforcement point.  Historical
    # repair, retries and direct callers cannot reintroduce an unpriced or
    # deterministically excluded card.
    cheap = apply_cheap_gate(discovery)
    if not cheap.approved:
        discovery.publication_status = cheap.status
        discovery.publication_reason = cheap.reason_code or "Card rejeitado pelo gate determinístico."
        _withdraw_signal(db, discovery, discovery.publication_reason)
        return None
    if override is None:
        payload = _rule_payload(discovery)
        discovery.rule_analysis_status = "completed"
        discovery.rule_analysis_result = payload
        discovery.rule_version = RULE_VERSION
    else:
        # Agent output is evidence-limited but can resolve card semantics.
        discovery.card_analysis_result = dict(override)
        discovery.card_analysis_status = "completed"
        discovery.card_analysis_updated_at = _now()
        payload = _effective_payload(discovery)

    publishable, reason = _publishable(discovery)
    discovery.publication_status = "published" if publishable else "rejected"
    discovery.publication_reason = reason
    if not publishable:
        _withdraw_signal(db, discovery, reason)
        return None

    listing = db.query(Listing).filter(
        Listing.source == _source_for(discovery), Listing.external_id == discovery.external_id,
    ).first()
    product = listing.product if listing and listing.audit_status == "audited" and listing.product else _resolve_product(db, payload)
    now = _now()
    if listing is None:
        listing = Listing(
            id=_stable_id(_source_for(discovery), discovery.external_id),
            source=_source_for(discovery), external_id=discovery.external_id,
            first_seen=now, created_at=now, status="active",
            audit_status="unaudited", evidence_level="card",
        )
        db.add(listing)

    # A card may refresh last_seen but must never replace richer detail fields.
    listing.last_seen = now
    listing.source_url = canonical_source_url(discovery.normalized_url or listing.source_url)
    if listing.audit_status != "audited":
        listing.status = "active"
        listing.product_id = product.id
        listing.market_cohort_id = product.market_cohort_id
        listing.category = payload["category"]
        listing.item_form = payload["item_form"]
        listing.classification_status = payload["classification_status"]
        listing.classification_confidence = float(payload.get("confidence") or 0)
        listing.taxonomy_version = HARDWARE_TAXONOMY_VERSION
        listing.schema_version = HARDWARE_SCHEMA_VERSION
        listing.title = discovery.title
        listing.price = discovery.price
        listing.condition = discovery.condition or None
        listing.attributes = payload.get("extracted_attributes") or {}
        listing.analysis_summary = payload.get("listing_summary") or discovery.title
        listing.analysis_status = "card"
        listing.analysis_updated_at = now
        listing.normalization_confidence = float(payload.get("confidence") or 0)
        listing.analytics_eligible = _market_eligible(payload, discovery)
        listing.exclusion_codes = [] if listing.analytics_eligible else ["UNVERIFIED_EVIDENCE"]
        listing.classification_evidence = payload.get("classification_evidence") or []
        listing.evidence_level = "card"
        listing.audit_status = "unaudited"
        listing.evidence_provenance = {
            "title": "search_card", "price": discovery.price_origin,
            "location": discovery.location_origin, "classification": "rule" if override is None else "agent",
        }
        if discovery.location:
            clean_loc = re.sub(r'\s*(?:hoje|ontem|\d{1,2}\s+de\s+[a-z]+|\d{1,2}/\d{1,2}).*$', '', discovery.location, flags=re.IGNORECASE).strip(' -,\t')
            location = clean_loc.rsplit("-", 1)
            listing.location_city = location[0].strip() or None
            state_raw = location[1].strip() if len(location) == 2 else None
            if state_raw:
                state_match = re.search(r'\b([A-Z]{2})\b', state_raw)
                listing.location_state = state_match.group(1) if state_match else state_raw[:2].upper()
            else:
                listing.location_state = None
    db.flush()

    discovery.listing_id = listing.id
    snapshot = db.query(ListingSnapshot).filter(
        ListingSnapshot.listing_discovery_id == discovery.id,
        ListingSnapshot.evidence_level == "card",
    ).first()
    if snapshot is None:
        snapshot = ListingSnapshot(
            listing_id=listing.id, pipeline_run_id=discovery.pipeline_run_id,
            listing_discovery_id=discovery.id, evidence_level="card", observed_at=now,
        )
        db.add(snapshot)
    snapshot.product_id = product.id
    snapshot.market_cohort_id = product.market_cohort_id
    snapshot.category = payload["category"]
    snapshot.item_form = payload["item_form"]
    snapshot.attributes = payload.get("extracted_attributes") or {}
    snapshot.classification_status = payload["classification_status"]
    snapshot.classification_confidence = float(payload.get("confidence") or 0)
    snapshot.taxonomy_version = HARDWARE_TAXONOMY_VERSION
    snapshot.schema_version = HARDWARE_SCHEMA_VERSION
    snapshot.analytics_eligible = _market_eligible(payload, discovery)
    snapshot.exclusion_codes = [] if snapshot.analytics_eligible else ["UNVERIFIED_EVIDENCE"]
    snapshot.classification_evidence = payload.get("classification_evidence") or []
    snapshot.price = discovery.price
    snapshot.status = "active"
    snapshot.observed_at = now
    return listing


def _source_for(discovery: ListingDiscovery) -> str:
    return "olx" if "olx" in (discovery.normalized_url or "").casefold() else "fixture"


def _withdraw_signal(db, discovery: ListingDiscovery, reason: str) -> None:
    signal = db.query(OpportunitySignal).filter(OpportunitySignal.listing_discovery_id == discovery.id).first()
    if signal:
        signal.stage = "withdrawn"
        signal.eligibility_reasons = [reason]
        signal.updated_at = _now()
        if signal.opportunity_id:
            opportunity = db.query(Opportunity).filter(Opportunity.id == signal.opportunity_id).first()
            if opportunity:
                opportunity.is_current = False
                opportunity.investigation_status = "withdrawn"


def project_run_cards(db, run_id: str, *, require_agent: bool = False, publish: bool = True) -> dict[str, int]:
    """Apply the saved-scope gate before exposing card-only projections.

    Disabled mode is fully deterministic and never creates model work. In
    required mode only a successful agent result may pass; failed or missing
    reviews remain withheld and therefore fail closed.
    """
    discoveries = db.query(ListingDiscovery).filter(ListingDiscovery.pipeline_run_id == run_id).all()
    published = rejected = withheld = approved = agent_pending = 0
    for discovery in discoveries:
        decision, details = evaluate_discovery_gate(discovery, require_agent=require_agent)
        _record_match(discovery, decision, details)
        if decision == "rejected":
            discovery.publication_status = details.get("publication_status") or "agent_rejected" if require_agent else details.get("publication_status") or "rejected_prefilter"
            discovery.publication_reason = details.get("reason")
            _withhold_existing_projection(db, discovery, discovery.publication_reason)
            rejected += 1
        elif decision != "confirmed":
            discovery.publication_status = "withheld_unverified"
            discovery.publication_reason = details.get("reason")
            _withhold_existing_projection(db, discovery, discovery.publication_reason)
            withheld += 1
            agent_pending += int(require_agent and discovery.card_analysis_status in {"not_requested", "pending", "running"})
        elif not publish:
            discovery.publication_status = "agent_approved" if require_agent else "approved"
            discovery.publication_reason = details.get("reason")
            approved += 1
        else:
            override = discovery.card_analysis_result if require_agent else None
            listing = project_discovery(db, discovery, override=override)
            if listing:
                published += 1
            else:
                rejected += 1
        if not require_agent and discovery.card_analysis_status in {"not_requested", "pending"}:
            discovery.card_analysis_status = "not_needed"
    db.flush()
    return {
        "published": published,
        "approved": approved,
        "rejected": rejected,
        "withheld": withheld,
        "agent_pending": agent_pending,
    }


def refresh_preliminary_signals(db, run_id: str) -> int:
    """Score only card observations with a usable preliminary cohort."""
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
    if not run:
        return 0
    discoveries = db.query(ListingDiscovery).filter(ListingDiscovery.pipeline_run_id == run_id).all()
    created = 0
    for discovery in discoveries:
        payload = _effective_payload(discovery)
        listing = discovery.listing
        if discovery.publication_status != "published" or not listing or not _market_eligible(payload, discovery):
            _withdraw_signal(db, discovery, "Evidência de card insuficiente para oportunidade preliminar.")
            continue
        market = db.query(MarketSnapshot).filter(
            MarketSnapshot.product_id == listing.product_id,
            MarketSnapshot.source == listing.source,
            MarketSnapshot.evidence_tier == "preliminary",
        ).order_by(MarketSnapshot.calculated_at.desc()).first()
        
        used_baseline = None
        if market and market.included_count >= MIN_MARKET_SAMPLE and market.median:
            used_baseline = market.median
        elif listing.product and listing.product.attributes.get("reference_price_brl"):
            used_baseline = float(listing.product.attributes["reference_price_brl"]) * 0.65

        if not used_baseline:
            _withdraw_signal(db, discovery, "Amostra preliminar insuficiente para estimar vantagem de preço.")
            continue
        edge = max(-1.0, min(1.0, (used_baseline - float(discovery.price)) / used_baseline))
        score = round(35.0 + min(25.0, float(payload.get("confidence") or 0) * 25.0) + 15.0 + max(0.0, edge) * 25.0, 2)
        qualifies = score >= 60.0 or edge >= 0.12
        signal = db.query(OpportunitySignal).filter(
            OpportunitySignal.profile_id == run.profile_id,
            OpportunitySignal.listing_discovery_id == discovery.id,
        ).first()
        if signal is None:
            signal = OpportunitySignal(
                id=_stable_id("signal", f"{run.profile_id}:{discovery.id}"), profile_id=run.profile_id,
                pipeline_run_id=run.id, listing_discovery_id=discovery.id,
            )
            db.add(signal)
        signal.preliminary_score = score
        signal.score_breakdown = {"scope": 35.0, "identity": min(25.0, float(payload.get("confidence") or 0) * 25.0), "price_evidence": 15.0, "price_edge": round(max(0.0, edge) * 25.0, 2)}
        signal.eligibility_reasons = [] if qualifies else ["PRELIMINARY_SCORE_BELOW_THRESHOLD"]
        signal.stage = "preliminary" if qualifies else "withdrawn"
        signal.full_flow_completed = False
        signal.updated_at = _now()
        created += int(qualifies)
    db.flush()
    return created
