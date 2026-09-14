"""Read-only database audit for categories, historical facts, and taxonomy integrity.

Generates structured diagnostic counts and summaries without exposing technical IDs
in user-facing interfaces.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict
from sqlalchemy.orm import Session

from packages.core.models import (
    Listing,
    ListingAnalysis,
    ListingSnapshot,
    MarketCohort,
    MarketSnapshot,
    MarketSnapshotMember,
    PipelineParserArtifact,
    Product,
)


def cleanup_expired_diagnostic_artifacts(db: Session, retention_days: int = 14) -> int:
    """Delete diagnostic parser artifacts older than retention window.
    
    Listing evidence, snapshots, sessions, and market facts are never touched.
    """
    cutoff = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(days=retention_days)
    deleted = (
        db.query(PipelineParserArtifact)
        .filter(PipelineParserArtifact.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted


def run_database_audit(db: Session) -> Dict[str, Any]:
    """Execute read-only data integrity and historical provenance audit."""
    # 1. Counts by category in Products and Listings
    products = db.query(Product).all()
    listings = db.query(Listing).all()

    category_product_counts: Dict[str, int] = {}
    for p in products:
        category_product_counts[p.category] = category_product_counts.get(p.category, 0) + 1

    category_listing_counts: Dict[str, int] = {}
    for l in listings:
        category_listing_counts[l.category] = category_listing_counts.get(l.category, 0) + 1

    # 2. Listings with multiple analyses
    listing_analysis_counts: Dict[str, int] = {}
    for a in db.query(ListingAnalysis).all():
        listing_analysis_counts[a.listing_id] = listing_analysis_counts.get(a.listing_id, 0) + 1
    multiple_analyses_count = sum(1 for count in listing_analysis_counts.values() if count > 1)

    # 3. Snapshots without corresponding analyses
    analyzed_listing_ids = set(listing_analysis_counts.keys())
    all_snapshots = db.query(ListingSnapshot).all()
    snapshots_without_analysis = sum(1 for s in all_snapshots if s.listing_id not in analyzed_listing_ids)

    # 4. Products used by incompatible categories
    incompatible_product_uses = 0
    product_map = {p.id: p.category for p in products}
    for l in listings:
        if l.product_id and l.product_id in product_map:
            if l.category != product_map[l.product_id]:
                incompatible_product_uses += 1

    # 5. Market snapshots with and without reproducible member lineage
    all_market_snapshots = db.query(MarketSnapshot).all()
    snapshots_with_lineage = 0
    snapshots_without_lineage = 0
    legacy_market_snapshots = 0
    for ms in all_market_snapshots:
        if getattr(ms, "is_legacy", False):
            legacy_market_snapshots += 1
        member_count = db.query(MarketSnapshotMember).filter(MarketSnapshotMember.market_snapshot_id == ms.id).count()
        if member_count > 0:
            snapshots_with_lineage += 1
        else:
            snapshots_without_lineage += 1

    # 6. Listings marked eligible that contain bundle/full system/parts signals
    suspect_eligible_count = 0
    for l in listings:
        if getattr(l, "analytics_eligible", False):
            item_form = getattr(l, "item_form", "standalone")
            cond = (l.condition or "").lower()
            if item_form in {"bundle", "parts", "accessory"} or cond in {"for_parts", "defeito", "sucata"}:
                suspect_eligible_count += 1

    # 7. Cohort counts
    cohort_count = db.query(MarketCohort).count()

    return {
        "summary": {
            "total_products": len(products),
            "total_listings": len(listings),
            "total_listing_snapshots": len(all_snapshots),
            "total_market_snapshots": len(all_market_snapshots),
            "total_cohorts": cohort_count,
        },
        "category_breakdown": {
            "products": category_product_counts,
            "listings": category_listing_counts,
        },
        "data_quality_diagnostics": {
            "listings_with_multiple_analyses": multiple_analyses_count,
            "snapshots_without_analysis": snapshots_without_analysis,
            "incompatible_product_category_crossings": incompatible_product_uses,
            "market_snapshots_with_member_lineage": snapshots_with_lineage,
            "market_snapshots_without_lineage": snapshots_without_lineage,
            "legacy_market_snapshots": legacy_market_snapshots,
            "suspect_eligible_listings_with_bundle_or_parts": suspect_eligible_count,
        },
    }
