import hashlib
import logging
import os
import re
import time
import uuid
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import unicodedata

logger = logging.getLogger(__name__)

from packages.ai.model_gateway import DeterministicLocalModelGateway, get_model_gateway
from packages.ai.metrics import model_metric_context
from packages.core.database import SessionLocal
from packages.core.models import (
    Listing,
    ListingSnapshot,
    MarketSnapshot,
    Opportunity,
    OpportunityEvaluation,
    PipelineRun,
    PipelineRunScope,
    PipelineRunScopeItem,
    Profile,
    Product,
    UserPreference,
    ListingAnalysis,
    PipelineStateTransition,
)
from packages.market.engine import compute_market_stats, compute_opportunity_score
from packages.marketplace.source import SearchQuery, get_marketplace_source
from packages.marketplace.source import OlxAccessError
from packages.retrieval.retriever import get_retriever
from packages.search.matching import evaluate_candidate
from packages.search.models import SearchCandidate, SearchPlanV1


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PipelineBlockedError(Exception):
    def __init__(self, error: OlxAccessError):
        super().__init__(str(error))
        self.error = error


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _fold_text(value: str | None) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return value.casefold()


def _scope_expected_tokens(scope_data: dict, canonical_product: Optional[Product]) -> list[str]:
    if canonical_product is not None:
        expected = " ".join(
            part for part in (canonical_product.model, canonical_product.variant or "") if part
        )
    else:
        expected = str(scope_data.get("query", ""))
    tokens = re.findall(r"[a-z0-9]+", _fold_text(expected))
    # Brand/category words are too broad to protect a saved scope. Model and
    # variant tokens containing digits are the useful disambiguators: 5600X,
    # 3060, 3080, 12GB, M1, etc.
    distinctive = [token for token in tokens if any(char.isdigit() for char in token)]
    return distinctive or [token for token in tokens if len(token) >= 4]


def _summary_matches_scope(summary_title: str, scope_data: dict, canonical_product: Optional[Product]) -> bool:
    # Broad notebook discovery is narrowed by the submitted catalogue after
    # listing details are collected. A title-only model gate here would reject
    # valid marketplace wording before that evidence can be compared.
    if scope_data.get("catalog_match"):
        return True
    expected_tokens = _scope_expected_tokens(scope_data, canonical_product)
    if not expected_tokens:
        return True
    title = _fold_text(summary_title)
    return all(token in title for token in expected_tokens)


def _is_recommendation_external_id(external_id: str) -> bool:
    return any(
        marker in (external_id or "").casefold()
        for marker in (
            "rec_detail_location=listing_no_result",
            "rec_detail_recommendation=",
            "rec_detail_engine=",
            "is_fallback=true",
        )
    )


def _default_preferences(db, profile_id: Optional[str]) -> Dict[str, Any]:
    """Resolve only the run owner's preferences; never borrow another profile's."""
    if profile_id:
        pref = db.query(UserPreference).filter(UserPreference.profile_id == profile_id).first()
        if pref and pref.preferences:
            return pref.preferences
        profile = db.query(Profile).filter(Profile.id == profile_id).first()
        if profile and profile.preferences:
            return profile.preferences
    return {
        "category_expertise": {},
        "max_capital": 99999.0,
        "min_desired_edge": 0.10,
        "preferred_location": "SP",
    }


def _resolve_product(db, normalized, detail, canonical_product: Optional[Product] = None) -> Product:
    if canonical_product is not None:
        return canonical_product

    from packages.catalog.resolver import resolve_product_and_cohort
    cat_prod, cohort, _ = resolve_product_and_cohort(
        db,
        category=normalized.category,
        title=detail.title,
        description=detail.description,
        attributes={**(normalized.extracted_attributes or {}), **(detail.attributes or {})},
    )
    if cat_prod:
        return cat_prod

    if normalized.product_id:
        product = db.query(Product).filter(Product.id == normalized.product_id).first()
        if product:
            return product

    product = (
        db.query(Product)
        .filter(
            Product.category == normalized.category,
            Product.brand == normalized.brand,
            Product.model == normalized.model,
            Product.variant == normalized.variant,
        )
        .first()
    )
    if product:
        if cohort and not product.market_cohort_id:
            product.market_cohort_id = cohort.id
        return product

    product_id = _stable_id(
        "prod-live",
        "|".join(
            [normalized.category, normalized.brand, normalized.model, normalized.variant or ""]
        ),
    )
    product = Product(
        id=product_id,
        category=normalized.category,
        brand=normalized.brand,
        family=normalized.model,
        model=normalized.model,
        variant=normalized.variant,
        display_name=" ".join(
            part for part in [normalized.brand, normalized.model, normalized.variant] if part
        ),
        attributes=normalized.extracted_attributes or detail.attributes or {},
        canonical_tier=2,
        market_cohort_id=cohort.id if cohort else None,
    )
    db.add(product)
    db.flush()
    return product


async def run_pipeline(
    run_id: str,
    source_type: str = "fixture",
    query: str = "",
    limit: int = 5,
    model_mode: str = "auto",
    investigate_limit: int = 2,
) -> Dict[str, Any]:
    """Execute one persisted ingest job.

    The function is deliberately usable by the worker and by focused tests;
    it does not depend on FastAPI background-task lifetime or request state.
    """
    db = SessionLocal()
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
    if not run:
        db.close()
        raise ValueError(f"Pipeline run {run_id} not found")

    started = time.monotonic()
    configuration = ((run.steps or [{}])[0]).get("configuration", {})
    if configuration.get("workload_mode") == "high_volume" or run.workload_mode == "high_volume":
        # High-volume discovery owns its own durable task lifecycle.  Keep the
        # established pipeline path untouched for immediate, small runs.
        db.close()
        from packages.pipeline.high_volume import run_high_volume_pipeline

        return await run_high_volume_pipeline(
            run_id=run_id,
            source_type=source_type,
            query=query,
            limit=limit,
            model_mode=model_mode,
            investigate_limit=investigate_limit,
        )
    run.status = "running"
    run.started_at = _now()
    run.steps = [{"configuration": configuration}]
    db.commit()

    try:
        app_mode = os.getenv("APP_MODE", "local").lower()
        use_local_model = model_mode == "local" or (
            model_mode == "auto" and app_mode == "local"
        )
        gateway = DeterministicLocalModelGateway() if use_local_model else get_model_gateway()
        source = get_marketplace_source(source_type)

        # The API stores immutable scope snapshots in the run configuration.
        # The legacy query/limit fields remain supported for old queued jobs.
        configured_scopes = configuration.get("scopes") or []
        if not configured_scopes:
            configured_scopes = [{
                "id": None,
                "name": "Busca avulsa",
                "marketplace": source_type,
                "query": query,
                "category": None,
                "min_price": None,
                "max_price": None,
                "sort": "recent",
                "limit": max(1, min(limit, 100)),
            }]

        if any(scope.get("catalog_match") for scope in configured_scopes):
            from packages.catalog import ensure_notebook_catalog

            ensure_notebook_catalog(db)
            db.commit()

        ingest_started = time.monotonic()
        processed = 0
        snapshots_created = 0
        normalized_count = 0
        current_listing_ids: list[str] = []
        seen_external_ids: set[str] = set()
        all_summaries = 0
        pending_details: list[tuple[Any, dict, Optional[Product], Any, asyncio.Task]] = []
        page_photos_by_listing: dict[str, list[dict]] = {}
        triage_semaphore = asyncio.Semaphore(2)

        async def run_triage(detail, category_hint=None):
            async with triage_semaphore:
                try:
                    result = await gateway.normalize_listing(
                        detail.title,
                        detail.description,
                        category_hint=category_hint or (detail.attributes.get("category") if detail.attributes else None),
                        listing_context={
                            "delivery_text": detail.delivery_text,
                            "delivery_evidence": [detail.delivery_text] if detail.delivery_text else [],
                            "seller_verification": detail.seller_verification,
                            "seller_evidence": detail.seller_evidence,
                            "seller_rating": detail.seller_rating,
                            "attributes": detail.attributes,
                        },
                    )
                    return result, "completed", None
                except Exception as exc:
                    # A model outage must not cause another OLX navigation or
                    # discard the already captured page. Local normalization is
                    # an explicit safe fallback and the failure is persisted.
                    fallback_gateway = DeterministicLocalModelGateway()
                    result = await fallback_gateway.normalize_listing(
                        detail.title,
                        detail.description,
                        category_hint=category_hint or (detail.attributes.get("category") if detail.attributes else None),
                        listing_context={
                            "delivery_text": detail.delivery_text,
                            "delivery_evidence": [detail.delivery_text] if detail.delivery_text else [],
                            "seller_verification": detail.seller_verification,
                            "seller_evidence": detail.seller_evidence,
                            "seller_rating": detail.seller_rating,
                            "attributes": detail.attributes,
                        },
                    )
                    return result, "failed", f"{type(exc).__name__}: {exc}"

        for scope_index, scope_data in enumerate(configured_scopes):
            scope_data = dict(scope_data)
            canonical_product = None
            if scope_data.get("product_id"):
                canonical_product = db.query(Product).filter(
                    Product.id == scope_data.get("product_id")
                ).first()
            scope_run = PipelineRunScope(
                id=f"{run_id}-scope-{scope_index + 1}-{uuid.uuid4().hex[:6]}",
                pipeline_run_id=run_id,
                search_scope_id=scope_data.get("id"),
                scope_snapshot=scope_data,
                status="running",
                started_at=_now(),
            )
            db.add(scope_run)
            db.flush()
            # Persist the scope before any remote navigation so a later OLX
            # block can retain prior work and receive a structured status.
            db.commit()
            search_query = SearchQuery(
                query=str(scope_data.get("query", "")),
                category=scope_data.get("category"),
                min_price=scope_data.get("min_price"),
                max_price=scope_data.get("max_price"),
                olx_pay_only=bool(scope_data.get("olx_pay_only")),
                delivery_only=bool(scope_data.get("delivery_only")),
                require_price=bool(scope_data.get("require_price", True)),
                sort=scope_data.get("sort", "recent"),
                limit=max(1, min(int(scope_data.get("limit", limit)), 100)),
            )
            logger.info("Busca marketplace: '%s' (limite=%s)", search_query.query, search_query.limit)
            try:
                summaries = await source.search(search_query)
                logger.info("Capturados %s anúncios na busca '%s'", len(summaries), search_query.query)
                diagnostics = getattr(source, "last_search_diagnostics", {}) or {}
                scope_run.search_url = getattr(source, "last_search_url", "") or ""
                scope_run.total_results = int(diagnostics.get("total_results", len(summaries)))
                scope_run.cards_detected = int(diagnostics.get("cards_detected", len(summaries)))
                scope_run.items_returned = len(summaries)
                all_summaries += len(summaries)

                for rank, summary in enumerate(summaries, start=1):
                    item = PipelineRunScopeItem(
                        pipeline_run_scope_id=scope_run.id,
                        external_id=summary.external_id,
                        rank=rank,
                        status="duplicate_scope" if summary.external_id in seen_external_ids else "discovered",
                    )
                    db.add(item)
                    if summary.external_id in seen_external_ids:
                        continue
                    seen_external_ids.add(summary.external_id)
                    if source_type == "olx" and _is_recommendation_external_id(summary.external_id):
                        item.status = "recommendation_filtered"
                        item.error_message = "Card recomendado da OLX, não pertence ao resultado da busca."
                        continue
                    if source_type == "olx" and not _summary_matches_scope(
                        summary.title, scope_data, canonical_product
                    ):
                        item.status = "scope_mismatch"
                        item.error_message = "Título não corresponde ao modelo esperado pelo escopo."
                        continue
                    try:
                        detail = await source.fetch_listing(summary.external_id)
                    except (FileNotFoundError, ValueError) as exc:
                        item.status = "detail_unavailable"
                        item.error_message = str(exc)
                        continue

                    triage_task = asyncio.create_task(run_triage(detail, scope_data.get("category")))
                    pending_details.append((item, scope_data, canonical_product, detail, triage_task))
                    continue

                scope_run.processed_count = sum(
                    1 for item in scope_run.items if item.status == "processed"
                )
                scope_run.status = "completed"
                scope_run.finished_at = _now()
                db.commit()
            except OlxAccessError as exc:
                db.rollback()
                blocked_scope = db.query(PipelineRunScope).filter(PipelineRunScope.id == scope_run.id).first()
                if blocked_scope:
                    blocked_scope.status = "blocked"
                    blocked_scope.error_code = exc.code
                    blocked_scope.error_message = str(exc.detail)
                    blocked_scope.finished_at = _now()
                    db.commit()
                raise PipelineBlockedError(exc) from exc
            except Exception as exc:
                db.rollback()
                scope_run = db.query(PipelineRunScope).filter(PipelineRunScope.id == scope_run.id).first()
                if scope_run:
                    scope_run.status = "failed"
                    scope_run.error_code = getattr(getattr(exc, "detail", None), "get", lambda *_: None)("code") if getattr(exc, "detail", None) else type(exc).__name__
                    scope_run.error_message = str(exc)
                    scope_run.finished_at = _now()
                    db.commit()
                raise

        # Detail capture and triage are intentionally split: all OLX
        # navigation remains guarded/serial, while the bounded model pool
        # processes already captured pages concurrently.
        triage_model_id = getattr(gateway, "model_extraction", "deterministic-local")
        for item, scope_data, canonical_product, detail, triage_task in pending_details:
            normalized, triage_status, triage_error = await triage_task
            if source_type == "olx" and not _summary_matches_scope(
                detail.title, scope_data, canonical_product
            ):
                item.status = "scope_mismatch"
                item.error_message = "Detalhe não corresponde ao modelo esperado pelo escopo."
                continue
            # Search scopes carry the validated plan snapshot.  Evaluate the
            # full plan only after detail attributes and model-extracted
            # attributes are available; title-only filtering is insufficient
            # for variants such as IPS vs TN or Wi-Fi 5 GHz support.
            match_status = "confirmed"
            match_details: dict[str, Any] = {}
            plan_payload = scope_data.get("plan")
            if isinstance(plan_payload, dict) and plan_payload.get("schema_version"):
                try:
                    plan = SearchPlanV1.model_validate(plan_payload)
                    candidate_attributes = dict(normalized.extracted_attributes or {})
                    candidate_attributes.update(detail.attributes or {})
                    candidate = SearchCandidate(
                        source=source_type,
                        external_id=str(detail.external_id),
                        title=detail.title,
                        price=detail.price,
                        location=", ".join(item for item in (detail.location_city, detail.location_state) if item),
                        condition=detail.condition,
                        url=detail.source_url,
                        description=detail.description,
                        attributes=candidate_attributes,
                    )
                    match = evaluate_candidate(candidate, plan)
                    match_status = match.status
                    match_details = match.model_dump()
                except (TypeError, ValueError) as exc:
                    match_status = "unverified"
                    match_details = {"status": "unverified", "reasons": [f"Plano não pôde ser aplicado: {type(exc).__name__}"]}
            # Model-free notebook runs rely on the catalogue as a second,
            # deterministic funnel after the generic price/category match.
            # No catalogue evidence means unverified, never a valid match.
            if scope_data.get("catalog_match"):
                from packages.catalog import find_catalog_notebook

                catalog_product = find_catalog_notebook(
                    db,
                    title=detail.title,
                    description=detail.description,
                    attributes={**(normalized.extracted_attributes or {}), **(detail.attributes or {})},
                )
                if catalog_product is None:
                    if match_status == "confirmed":
                        match_status = "unverified"
                    match_details = {
                        **match_details,
                        "status": match_status,
                        "catalog": {"status": "unverified"},
                        "reasons": [*(match_details.get("reasons") or []), "Modelo não identificado no catálogo de notebooks."],
                    }
                else:
                    canonical_product = catalog_product
                    match_details = {
                        **match_details,
                        "catalog": {"status": "confirmed", "product_name": catalog_product.display_name},
                    }
            item.match_status = match_status
            item.match_details = match_details
            from packages.classification.eligibility import evaluate_analytics_eligibility

            eligibility = evaluate_analytics_eligibility(
                category=normalized.category,
                item_form=normalized.item_form,
                classification_status=normalized.classification_status,
                price=detail.price,
                condition=detail.condition,
                attributes={**(normalized.extracted_attributes or {}), **(detail.attributes or {})},
            )

            product = _resolve_product(db, normalized, detail, canonical_product=canonical_product)
            listing = (
                db.query(Listing)
                .filter(
                    Listing.source == source_type,
                    Listing.external_id == detail.external_id,
                )
                .first()
            )
            now = _now()
            if listing is None:
                listing = Listing(
                    id=_stable_id(source_type, detail.external_id),
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
            listing.attributes = {
                **(normalized.extracted_attributes or {}),
                **(detail.attributes or {}),
            }
            listing.delivery_status = normalized.delivery_status
            listing.delivery_evidence = normalized.delivery_evidence
            listing.seller_verification = normalized.seller_verification
            listing.seller_signal_level = normalized.seller_signal_level
            listing.seller_evidence = normalized.seller_evidence
            listing.analysis_summary = normalized.listing_summary
            listing.analysis_status = triage_status
            listing.analysis_updated_at = now
            listing.source_url = detail.source_url
            listing.last_seen = now
            listing.status = "active"
            listing.evidence_level = "detail"
            listing.audit_status = "audited"
            listing.evidence_provenance = {"detail": "listing_page", "classification": "detail_normalization"}
            listing.last_audited_at = now
            listing.normalization_confidence = normalized.confidence
            page_photos_by_listing[listing.id] = detail.photos[:5]
            db.flush()
            item.listing_id = listing.id
            item.status = "processed"
            triage_result = normalized.model_dump()
            if triage_error:
                triage_result["error"] = triage_error
            db.add(ListingAnalysis(
                id=_stable_id("analysis", f"{run_id}:{listing.id}:triage"),
                listing_id=listing.id,
                pipeline_run_id=run_id,
                stage="triage",
                status=triage_status,
                model_id=triage_model_id,
                result=triage_result,
                photos_examined=0,
                external_navigation_count=detail.external_navigation_count,
                created_at=now,
            ))
            snap = ListingSnapshot(
                listing_id=listing.id,
                pipeline_run_id=run_id,
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
            )
            db.add(snap)
            db.flush()
            # Unverified/rejected details remain persisted for audit and
            # market context, but only confirmed matches can become an
            # opportunity for this search execution.
            if match_status == "confirmed":
                current_listing_ids.append(listing.id)
            logger.info("Anúncio '%s' (R$ %s) -> %s", detail.title, detail.price, match_status)
            processed += 1
            normalized_count += 1
            snapshots_created += 1
            db.commit()

        for persisted_scope in db.query(PipelineRunScope).filter(PipelineRunScope.pipeline_run_id == run_id).all():
            persisted_scope.processed_count = sum(
                1 for scope_item in persisted_scope.items if scope_item.status == "processed"
            )
        db.commit()

        run.steps = [
            run.steps[0],
            {
                "name": "source_ingest",
                "status": "completed",
                "duration_seconds": round(time.monotonic() - ingest_started, 3),
                "items_in": all_summaries,
                "items_out": processed,
                "source": source_type,
                "scopes": len(configured_scopes),
                "deduplicated_items": len(seen_external_ids),
            },
        ]
        db.commit()

        stats_started = time.monotonic()
        from packages.market.engine import calculate_market_snapshot_from_snapshots
        from packages.core.hardware_taxonomy import supports_market_analysis
        source_product_ids = [
            row[0]
            for row in db.query(Listing.product_id)
            .filter(Listing.source == source_type, Listing.product_id.is_not(None))
            .distinct()
            .all()
        ]
        products = db.query(Product).filter(Product.id.in_(source_product_ids)).all() if source_product_ids else []
        product_stats: Dict[str, Dict[str, Any]] = {}
        market_snapshots_map: Dict[str, Any] = {}
        for product in products:
            if not supports_market_analysis(product.category):
                continue
            ms = calculate_market_snapshot_from_snapshots(
                db,
                product_id=product.id,
                market_cohort_id=product.market_cohort_id,
                category=product.category,
                source=source_type,
                pipeline_run_id=run_id,
                window_days=30,
            )
            market_snapshots_map[product.id] = ms
            product_stats[product.id] = {
                "sample_size": ms.sample_size,
                "active_count": ms.active_count,
                "disappeared_count": ms.disappeared_count,
                "asking_median": ms.asking_median,
                "estimated_clearing_value": ms.estimated_clearing_value,
                "fast_sale_value": ms.fast_sale_value,
                "robust_center": ms.robust_center,
                "p10": ms.p10,
                "p25": ms.p25,
                "median": ms.median,
                "p75": ms.p75,
                "p90": ms.p90,
                "mad": ms.mad,
                "market_heat": ms.market_heat,
                "heat_band": ms.heat_band,
                "confidence": ms.confidence,
                "listing_velocity": ms.listing_velocity,
                "disappearance_velocity": ms.disappearance_velocity,
                "median_visible_duration_days": ms.median_visible_duration_days,
                "price_trend_30d": ms.price_trend_30d,
                "category": product.category,
            }
        db.commit()
        run.steps = [
            *run.steps,
            {
                "name": "market_statistics",
                "status": "completed",
                "duration_seconds": round(time.monotonic() - stats_started, 3),
                "items_in": len(products),
                "items_out": len(products),
                "source": source_type,
            },
        ]
        db.commit()

        score_started = time.monotonic()
        preferences = _default_preferences(db, run.profile_id)
        opportunities = []
        for listing_id in dict.fromkeys(current_listing_ids):
            listing = db.query(Listing).filter(
                Listing.id == listing_id, Listing.source == source_type
            ).first()
            if not listing or not listing.product_id or listing.status != "active":
                continue
            stats = product_stats.get(listing.product_id)
            product = db.query(Product).filter(Product.id == listing.product_id).first()
            if not stats or not product:
                continue
            freshness = max(0.0, (_now() - listing.first_seen).total_seconds() / 86400.0)
            score, breakdown, edge, explanation = compute_opportunity_score(
                listing_price=listing.price,
                market_stats=stats,
                preferences=preferences,
                condition=listing.condition,
                freshness_days=freshness,
                is_preferred_location=bool(preferences.get("preferred_location")) and listing.location_state == preferences.get("preferred_location"),
                risk_factors=["whatsapp_scam"] if "WHATS" in listing.title.upper() else [],
            )
            qualifies = score >= 60.0 or edge >= 0.12
            latest_snap = db.query(ListingSnapshot).filter(ListingSnapshot.listing_id == listing.id).order_by(ListingSnapshot.observed_at.desc()).first()
            ms = market_snapshots_map.get(product.id)

            opportunity = db.query(Opportunity).filter(Opportunity.id == _stable_id("opp", listing.id)).first()
            if opportunity is None:
                opportunity = Opportunity(
                    id=_stable_id("opp", listing.id),
                    listing_id=listing.id,
                    product_id=product.id,
                    investigation={},
                )
                db.add(opportunity)
            opportunity.listing_snapshot_id = latest_snap.id if latest_snap else None
            opportunity.market_snapshot_id = ms.id if ms else None
            opportunity.market_cohort_id = product.market_cohort_id
            opportunity.category = product.category
            opportunity.source = source_type
            opportunity.last_pipeline_run_id = run_id
            opportunity.is_current = qualifies
            opportunity.price_edge = edge
            opportunity.final_score = score
            opportunity.market_heat = stats["market_heat"]
            opportunity.heat_band = stats["heat_band"]
            opportunity.confidence = stats["confidence"]
            opportunity.score_breakdown = breakdown
            opportunity.explanation = explanation
            opportunity.investigation_status = "pending" if qualifies else "not_qualified"
            opportunity.computed_at = _now()
            db.flush()
            db.add(OpportunityEvaluation(
                opportunity_id=opportunity.id,
                pipeline_run_id=run_id,
                listing_id=listing.id,
                product_id=product.id,
                listing_snapshot_id=latest_snap.id if latest_snap else None,
                market_snapshot_id=ms.id if ms else None,
                market_cohort_id=product.market_cohort_id,
                category=product.category,
                price_edge=edge,
                final_score=score,
                score_breakdown=breakdown,
                investigation={},
                status="qualified" if qualifies else "not_qualified",
                evaluated_at=_now(),
            ))
            if qualifies:
                logger.info("Oportunidade encontrada: '%s' por R$ %s (score %.2f)", listing.title, listing.price, score)
                opportunities.append((opportunity, listing, product, stats))
        db.commit()
        run.opportunities_found = len(opportunities)
        run.steps = [
            *run.steps,
            {
                "name": "opportunity_scoring",
                "status": "completed",
                "duration_seconds": round(time.monotonic() - score_started, 3),
                "items_in": len(dict.fromkeys(current_listing_ids)),
                "items_out": len(opportunities),
                "source": source_type,
            },
        ]
        db.commit()

        investigation_started = time.monotonic()
        retriever = get_retriever()
        ordered = sorted(opportunities, key=lambda row: row[0].final_score, reverse=True)
        investigation_count = len(ordered) if use_local_model else min(investigate_limit, len(ordered))
        investigation_semaphore = asyncio.Semaphore(2)

        async def investigate_one(opportunity, listing, product, stats):
            snippets = await retriever.search(
                query=f"{product.display_name} common defects",
                category=product.category,
                limit=3,
                db=db,
            )
            listing_dict = {
                "id": listing.id,
                "price": listing.price,
                "title": listing.title,
                "description": listing.description,
                "location_city": listing.location_city,
                "location_state": listing.location_state,
                "condition": listing.condition,
                "source_url": listing.source_url,
                "delivery_status": listing.delivery_status,
                "delivery_evidence": listing.delivery_evidence,
                "seller_name": listing.seller_name,
                "seller_rating": listing.seller_rating,
                "seller_verification": listing.seller_verification,
                "seller_signal_level": listing.seller_signal_level,
                "seller_evidence": listing.seller_evidence,
                "photos": page_photos_by_listing.get(listing.id, []),
            }
            async with investigation_semaphore:
                with model_metric_context(pipeline_run_id=run_id, origin="pipeline"):
                    try:
                        result = await gateway.investigate_opportunity(
                            listing_dict=listing_dict,
                            product_dict={"id": product.id, "display_name": product.display_name},
                            market_stats=stats,
                            knowledge_snippets=[item.model_dump() for item in snippets],
                        )
                        return result, None
                    except Exception as exc:
                        fallback_gateway = DeterministicLocalModelGateway()
                        fallback = await fallback_gateway.investigate_opportunity(
                            listing_dict=listing_dict,
                            product_dict={"id": product.id, "display_name": product.display_name},
                            market_stats=stats,
                            knowledge_snippets=[item.model_dump() for item in snippets],
                        )
                        fallback.status = "failed"
                        fallback.model_id = getattr(gateway, "model_investigation", "configured")
                        return fallback, f"{type(exc).__name__}: {exc}"

        investigation_tasks = [
            asyncio.create_task(investigate_one(opportunity, listing, product, stats))
            for opportunity, listing, product, stats in ordered[:investigation_count]
        ]
        for (opportunity, listing, product, stats), investigation_task in zip(
            ordered[:investigation_count], investigation_tasks
        ):
            result, investigation_error = await investigation_task
            investigation = result.model_dump()
            if investigation_error:
                investigation["error"] = investigation_error
            opportunity.investigation = investigation
            opportunity.investigation_status = result.status
            db.add(ListingAnalysis(
                id=_stable_id("analysis", f"{run_id}:{listing.id}:deep"),
                listing_id=listing.id,
                pipeline_run_id=run_id,
                stage="deep_analysis",
                status=result.status,
                model_id=getattr(gateway, "model_investigation", result.model_id),
                result=investigation,
                photos_examined=int((investigation.get("page_scope") or {}).get("photos_examined", 0)),
                external_navigation_count=int((investigation.get("page_scope") or {}).get("external_navigation_count", 0)),
                created_at=_now(),
            ))
            evaluation = db.query(OpportunityEvaluation).filter(
                OpportunityEvaluation.opportunity_id == opportunity.id,
                OpportunityEvaluation.pipeline_run_id == run_id,
            ).order_by(OpportunityEvaluation.id.desc()).first()
            if evaluation:
                evaluation.investigation = investigation
                evaluation.status = result.status
        for opportunity, _, _, _ in ordered[investigation_count:]:
            opportunity.investigation_status = "deferred"
        db.commit()
        run.steps = [
            *run.steps,
            {
                "name": "agent_investigation",
                "status": "completed",
                "duration_seconds": round(time.monotonic() - investigation_started, 3),
                "items_in": len(opportunities),
                "items_out": investigation_count,
                "model_mode": "local" if use_local_model else "configured",
                "source": source_type,
            },
        ]

        run.status = "completed"
        run.finished_at = _now()
        run.duration_seconds = round(time.monotonic() - started, 3)
        run.processed_count = processed
        run.snapshots_created = snapshots_created
        run.products_normalized = normalized_count
        run.opportunities_found = len(opportunities)
        db.commit()
        logger.info("Pipeline %s concluído: %s oportunidades geradas em %.2fs", run_id, len(opportunities), run.duration_seconds)
        return {
            "processed": processed,
            "snapshots": snapshots_created,
            "products_normalized": normalized_count,
            "opportunities": len(opportunities),
            "scopes": len(configured_scopes),
        }
    except PipelineBlockedError as exc:
        db.rollback()
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if run:
            run.status = "blocked"
            run.finished_at = _now()
            run.duration_seconds = round(time.monotonic() - started, 3)
            run.error_code = exc.error.code
            run.error_message = str(exc.error.detail)
            run.steps = [*run.steps, {
                "name": "source_ingest", "status": "blocked", "source": source_type,
                "error": {"code": exc.error.code, **exc.error.detail},
            }]
            run.processed_count = processed
            run.snapshots_created = snapshots_created
            run.products_normalized = normalized_count
            db.commit()
        return {
            "processed": processed,
            "snapshots": snapshots_created,
            "products_normalized": normalized_count,
            "opportunities": 0,
            "scopes": len(configured_scopes),
            "status": "blocked",
            "error": {"code": exc.error.code, **exc.error.detail},
        }
    except Exception as exc:
        db.rollback()
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if run:
            run.status = "failed"
            run.finished_at = _now()
            run.duration_seconds = round(time.monotonic() - started, 3)
            run.error_message = f"{type(exc).__name__}: {exc}"
            db.commit()
        raise
    finally:
        db.close()


def cancel_pipeline_run(run_id: str, actor: str = "operator") -> dict[str, Any]:
    db = SessionLocal()
    try:
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if not run:
            raise ValueError(f"Pipeline run {run_id} not found")
        if run.workload_mode == "high_volume":
            from packages.pipeline.high_volume import cancel_high_volume_run
            return cancel_high_volume_run(run_id, actor=actor)

        if run.status in {"completed", "completed_partial", "failed", "blocked", "cancelled"}:
            return {"id": run.id, "status": run.status, "message": "Pipeline já finalizado."}

        from_status = run.status
        run.status = "cancelled"
        run.finished_at = _now()
        if run.started_at and run.finished_at:
            run.duration_seconds = max(0.0, (run.finished_at - run.started_at).total_seconds())

        db.add(PipelineStateTransition(
            pipeline_run_id=run.id,
            from_status=from_status,
            to_status="cancelled",
            reason_code="operator_cancel",
            occurred_at=_now(),
            actor=actor,
            message="Cancelado pelo operador.",
        ))
        db.commit()
        db.refresh(run)
        return {"id": run.id, "status": run.status, "message": "Pipeline cancelado pelo operador."}
    finally:
        db.close()
