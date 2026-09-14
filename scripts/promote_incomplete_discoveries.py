"""Script to promote valid incomplete search card discoveries into canonical flow.

This script reads ListingDiscovery records from high-volume pipeline runs that have
completed card analysis and valid prices, filters out scrap/parts/accessories noise,
resolves or links them to products/catalog, persists canonical Listings, updates
MarketSnapshots, and calculates unverified Opportunities.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

from packages.catalog.notebooks import find_catalog_notebook
from packages.core.database import SessionLocal
from packages.core.models import (
    Listing,
    ListingAnalysis,
    ListingDiscovery,
    ListingSnapshot,
    MarketSnapshot,
    Opportunity,
    OpportunityEvaluation,
    PipelineRun,
    Product,
    Profile,
)
from packages.market.engine import compute_market_stats, compute_opportunity_score

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("promote_discoveries")

PART_KEYWORDS = [
    r"\bcarca[cç]a\b",
    r"\bbateria\b",
    r"\bfonte\b",
    r"\bcarregador\b",
    r"\btela\b",
    r"\bdisplay\b",
    r"\bteclado\b",
    r"\bplaca\s*m[aã]e\b",
    r"\bcooler\b",
    r"\bpe[cç]as\b",
    r"\bsucata\b",
    r"\bpara\s*retirada\b",
    r"\bcom\s*defeito\b",
    r"\bn[aã]o\s*liga\b",
    r"\bquebrad[oa]\b",
    r"\bcompro\b",
    r"\bprocuro\b",
    r"\bcabo\b",
    r"\bcapa\b",
    r"\bcase\b",
    r"\bsuporte\b",
]
PART_PATTERN = re.compile("|".join(PART_KEYWORDS), re.IGNORECASE)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _stable_id(prefix: str, key: str) -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _parse_location(location_str: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not location_str:
        return None, None
    parts = [p.strip() for p in location_str.split("-")]
    if len(parts) >= 2:
        city = parts[0]
        state = parts[-1].upper()
        return city, state
    return location_str.strip(), None


def is_clean_notebook(discovery: ListingDiscovery) -> Tuple[bool, str]:
    if not discovery.price or discovery.price <= 0:
        return False, "invalid_price"

    analysis = discovery.card_analysis_result or {}
    category = (analysis.get("category") or "").lower()
    title = discovery.title or ""
    extracted_attr = analysis.get("extracted_attributes") or {}
    component = (extracted_attr.get("component_type") or "").lower()

    if category and category not in ("notebook", "laptop"):
        return False, f"category_{category}"

    if component in ("bateria", "fonte", "tela", "teclado", "peca", "placa"):
        return False, f"component_{component}"

    if PART_PATTERN.search(title):
        return False, "title_part_or_damage_keyword"

    return True, "valid_notebook"


def resolve_or_create_product(
    db,
    discovery: ListingDiscovery,
    analysis: Dict[str, Any],
) -> Product:
    title = discovery.title or ""
    extracted = analysis.get("extracted_attributes") or {}

    # 1. Check catalog matching first
    catalog_product = find_catalog_notebook(db, title=title, description="", attributes=extracted)
    if catalog_product:
        return catalog_product

    # 2. Check if a dynamic product with brand/model/variant exists
    brand = analysis.get("brand") or "Generic"
    model = analysis.get("model") or "Notebook"
    variant = analysis.get("variant")
    category = "notebook"

    product = (
        db.query(Product)
        .filter(
            Product.category == category,
            Product.brand == brand,
            Product.model == model,
            Product.variant == variant,
        )
        .first()
    )
    if product:
        return product

    # 3. Create a tier 2 product
    product_id = _stable_id("prod-card", f"{category}|{brand}|{model}|{variant or ''}")
    existing_by_id = db.query(Product).filter(Product.id == product_id).first()
    if existing_by_id:
        return existing_by_id

    display_parts = [part for part in [brand, model, variant] if part and part != "UNKNOWN"]
    display_name = " ".join(display_parts) if display_parts else title[:60]

    product = Product(
        id=product_id,
        category=category,
        brand=brand,
        family=model,
        model=model,
        variant=variant,
        display_name=display_name,
        attributes=extracted,
        canonical_tier=2,
    )
    db.add(product)
    db.flush()
    return product


def promote_discoveries(
    pipeline_run_id: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        query = db.query(ListingDiscovery).filter(
            ListingDiscovery.card_analysis_status == "completed",
            ListingDiscovery.price > 0,
        )
        if pipeline_run_id:
            query = query.filter(ListingDiscovery.pipeline_run_id == pipeline_run_id)

        all_candidates = query.all()
        logger.info("Found %d candidates with completed analysis and price > 0", len(all_candidates))

        valid_cards: List[ListingDiscovery] = []
        filtered_out: Dict[str, int] = {}

        for card in all_candidates:
            is_valid, reason = is_clean_notebook(card)
            if is_valid:
                valid_cards.append(card)
            else:
                filtered_out[reason] = filtered_out.get(reason, 0) + 1

        logger.info("Filtered out: %s", filtered_out)
        logger.info("Eligible clean notebooks to promote: %d", len(valid_cards))

        if dry_run:
            logger.info("[DRY RUN] No database changes committed.")
            return {
                "total_candidates": len(all_candidates),
                "filtered_out": filtered_out,
                "promoted_count": len(valid_cards),
                "dry_run": True,
            }

        now = _now()
        promoted_listings: List[Listing] = []
        affected_product_ids: set[str] = set()

        for card in valid_cards:
            analysis = card.card_analysis_result or {}
            product = resolve_or_create_product(db, card, analysis)
            affected_product_ids.add(product.id)

            source_type = "olx"
            listing_id = _stable_id("olx", card.external_id)

            listing = db.query(Listing).filter(Listing.id == listing_id).first()
            if listing is None:
                listing = Listing(
                    id=listing_id,
                    source=source_type,
                    external_id=card.external_id,
                    first_seen=card.first_observed_at or now,
                    created_at=now,
                )
                db.add(listing)

            city, state = _parse_location(card.location)

            listing.marketplace_item_id = card.marketplace_item_id or card.external_id
            listing.product_id = product.id
            listing.title = card.title
            listing.description = analysis.get("listing_summary", "")
            listing.price = card.price
            listing.location_state = state
            listing.location_city = city
            listing.seller_name = "UNKNOWN"
            listing.seller_rating = None
            listing.condition = "UNKNOWN"
            listing.attributes = analysis.get("extracted_attributes") or {}
            listing.delivery_status = "UNKNOWN"
            listing.delivery_evidence = []
            listing.seller_verification = "UNKNOWN"
            listing.seller_signal_level = "UNKNOWN"
            listing.seller_evidence = []
            listing.analysis_summary = analysis.get("listing_summary", "Search card discovery promoted.")
            listing.analysis_status = "unverified"
            listing.analysis_updated_at = now
            listing.source_url = card.normalized_url or ""
            listing.last_seen = now
            listing.status = "active"
            confidence = float(analysis.get("confidence") or 0.5)
            listing.normalization_confidence = min(confidence, 0.7)

            db.flush()

            # Add ListingSnapshot
            db.add(
                ListingSnapshot(
                    listing_id=listing.id,
                    pipeline_run_id=card.pipeline_run_id,
                    price=card.price,
                    status="active",
                    observed_at=now,
                )
            )

            # Add ListingAnalysis
            analysis_id = f"analysis-{uuid.uuid4().hex[:20]}"
            db.add(
                ListingAnalysis(
                    id=analysis_id,
                    listing_id=listing.id,
                    pipeline_run_id=card.pipeline_run_id,
                    stage="triage",
                    status="unverified",
                    model_id=card.card_analysis_model_id or "deterministic-local",
                    result=analysis,
                    photos_examined=0,
                    external_navigation_count=0,
                    created_at=now,
                )
            )

            promoted_listings.append(listing)

        db.commit()
        logger.info("Persisted %d listings and snapshots.", len(promoted_listings))

        # Recompute MarketSnapshots for affected products
        logger.info("Recomputing MarketSnapshots for %d products...", len(affected_product_ids))
        for product_id in affected_product_ids:
            product = db.query(Product).filter(Product.id == product_id).first()
            if not product:
                continue
            listings = db.query(Listing).filter(
                Listing.source == "olx", Listing.product_id == product.id
            ).all()
            active = [item.price for item in listings if item.status == "active" and item.price]
            disappeared = [item.price for item in listings if item.status == "disappeared" and item.price]
            durations = [
                (item.last_seen - item.first_seen).total_seconds() / 86400.0
                for item in listings
                if item.first_seen and item.last_seen
            ]
            stats = compute_market_stats(active, disappeared, durations, window_days=30)
            stats["category"] = product.category

            snapshot_run_id = pipeline_run_id or (promoted_listings[0].pipeline_run_id if promoted_listings else None)
            db.add(
                MarketSnapshot(
                    product_id=product.id,
                    source="olx",
                    pipeline_run_id=snapshot_run_id,
                    calculated_at=now,
                    **{k: v for k, v in stats.items() if k != "category"},
                )
            )

        db.commit()

        # Generate / Update Opportunities for unverified listings
        logger.info("Generating Opportunities for promoted listings...")
        profile = db.query(Profile).first()
        preferences = (profile.preferences if profile else {}) or {}

        opportunities_created = 0
        for listing in promoted_listings:
            product = db.query(Product).filter(Product.id == listing.product_id).first()
            if not product:
                continue

            active_listings = db.query(Listing).filter(
                Listing.source == "olx", Listing.product_id == product.id
            ).all()
            active_prices = [item.price for item in active_listings if item.status == "active" and item.price]
            stats = compute_market_stats(active_prices, [], [], window_days=30)
            stats["category"] = product.category

            freshness = (now - (listing.first_seen or now)).total_seconds() / 86400.0
            score, breakdown, edge, explanation = compute_opportunity_score(
                listing_price=listing.price,
                market_stats=stats,
                preferences=preferences,
                condition="UNKNOWN",
                freshness_days=freshness,
                is_preferred_location=bool(preferences.get("preferred_location")) and listing.location_state == preferences.get("preferred_location"),
                risk_factors=["unverified_search_card"],
            )

            score = round(min(score, 65.0), 2)
            qualifies = score >= 50.0 or edge >= 0.10

            opp_id = _stable_id("opp", listing.id)
            opportunity = db.query(Opportunity).filter(Opportunity.id == opp_id).first()
            if opportunity is None:
                opportunity = Opportunity(
                    id=opp_id,
                    listing_id=listing.id,
                    product_id=product.id,
                    investigation={"note": "Search card unverified discovery"},
                )
                db.add(opportunity)

            opportunity.source = "olx"
            opportunity.last_pipeline_run_id = pipeline_run_id
            opportunity.is_current = qualifies
            opportunity.price_edge = edge
            opportunity.final_score = score
            opportunity.market_heat = stats["market_heat"]
            opportunity.heat_band = stats["heat_band"]
            opportunity.confidence = stats["confidence"]
            opportunity.score_breakdown = breakdown
            opportunity.explanation = f"[Card Unverified] {explanation}"
            opportunity.investigation_status = "not_qualified"
            opportunity.computed_at = now

            db.add(
                OpportunityEvaluation(
                    opportunity_id=opportunity.id,
                    pipeline_run_id=pipeline_run_id,
                    listing_id=listing.id,
                    product_id=product.id,
                    price_edge=edge,
                    final_score=score,
                    score_breakdown=breakdown,
                    investigation={"note": "Search card unverified evaluation"},
                    status="qualified" if qualifies else "not_qualified",
                    evaluated_at=now,
                )
            )
            opportunities_created += 1

        db.commit()
        logger.info("Opportunities created/updated: %d", opportunities_created)

        return {
            "total_candidates": len(all_candidates),
            "filtered_out": filtered_out,
            "promoted_listings": len(promoted_listings),
            "affected_products": len(affected_product_ids),
            "opportunities_created": opportunities_created,
            "dry_run": False,
        }
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Promote incomplete discovery cards to normal flow.")
    parser.add_argument("--run-id", type=str, default="pipe-cf4edfec", help="Specific PipelineRun ID (default: pipe-cf4edfec)")
    parser.add_argument("--dry-run", action="store_true", help="Run without persisting changes")
    args = parser.parse_args()

    result = promote_discoveries(pipeline_run_id=args.run_id, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
