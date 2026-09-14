"""Unified catalog and market cohort resolver across all hardware categories.

Dispatches deterministic resolution to the corresponding catalog module.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
from sqlalchemy.orm import Session

from packages.catalog import cpu as cpu_catalog
from packages.catalog import gpu as gpu_catalog
from packages.catalog import notebooks as notebook_catalog
from packages.catalog import ram as ram_catalog
from packages.core.hardware_taxonomy import HardwareCategory, normalize_category
from packages.core.models import MarketCohort, Product


def resolve_product_and_cohort(
    db: Session,
    *,
    category: Any,
    title: str,
    description: str = "",
    attributes: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Product], Optional[MarketCohort], Dict[str, Any]]:
    """Resolve both Product and MarketCohort for a classified listing observation.

    Returns:
        (resolved_product, resolved_cohort, explanation_dict)
    """
    cat = normalize_category(category)

    if cat == HardwareCategory.GPU:
        cohort = gpu_catalog.resolve_market_cohort(db, title=title, description=description, attributes=attributes)
        product = gpu_catalog.resolve_exact_product(db, title=title, description=description, attributes=attributes)
        explanation = gpu_catalog.explain_resolution(title=title, product=product, cohort=cohort)
        return product, cohort, explanation

    elif cat == HardwareCategory.CPU:
        cohort = cpu_catalog.resolve_market_cohort(db, title=title, description=description, attributes=attributes)
        product = cpu_catalog.resolve_exact_product(db, title=title, description=description, attributes=attributes)
        explanation = cpu_catalog.explain_resolution(title=title, product=product, cohort=cohort)
        return product, cohort, explanation

    elif cat == HardwareCategory.RAM:
        cohort = ram_catalog.resolve_market_cohort(db, title=title, description=description, attributes=attributes)
        product = ram_catalog.resolve_exact_product(db, title=title, description=description, attributes=attributes)
        explanation = ram_catalog.explain_resolution(title=title, product=product, cohort=cohort)
        return product, cohort, explanation

    elif cat == HardwareCategory.NOTEBOOK:
        product = notebook_catalog.resolve_exact_product(db, title=title, description=description, attributes=attributes)
        cohort = notebook_catalog.resolve_market_cohort(db, title=title, description=description, attributes=attributes)
        explanation = notebook_catalog.explain_resolution(title=title, product=product, cohort=cohort)
        return product, cohort, explanation

    return None, None, {"status": "unverified", "reason": f"Sem catálogo canônico para a categoria '{cat.value}'."}
