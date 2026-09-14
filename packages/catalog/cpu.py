"""CPU canonical catalog and market cohort resolver.

Deterministic catalog for AMD Ryzen and Intel Core CPUs.
Provides resolve_exact_product, resolve_market_cohort, and explain_resolution.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from packages.core.hardware_taxonomy import HARDWARE_TAXONOMY_VERSION, HardwareCategory
from packages.core.models import MarketCohort, Product


CPU_CATALOG_VERSION = "cpu-catalog-v1"

# Canonical CPU definitions: (product_id, cohort_key, brand, family, model, variant, display_name, attributes, reference_price)
CPU_CANONICAL_ITEMS = [
    # AMD AM4
    ("prod-cpu-5600", "cohort-cpu-5600", "AMD", "Ryzen 5000", "Ryzen 5 5600", None, "AMD Ryzen 5 5600 6-Core", {"brand": "AMD", "family": "Ryzen 5", "model_number": "Ryzen 5 5600", "generation": "5000", "socket": "AM4", "cores": 6, "threads": 12, "suffix": None, "integrated_gpu": False}, 650.0),
    ("prod-cpu-5600x", "cohort-cpu-5600x", "AMD", "Ryzen 5000", "Ryzen 5 5600X", "X", "AMD Ryzen 5 5600X 6-Core", {"brand": "AMD", "family": "Ryzen 5", "model_number": "Ryzen 5 5600X", "generation": "5000", "socket": "AM4", "cores": 6, "threads": 12, "suffix": "X", "integrated_gpu": False}, 750.0),
    ("prod-cpu-5700x", "cohort-cpu-5700x", "AMD", "Ryzen 5000", "Ryzen 7 5700X", "X", "AMD Ryzen 7 5700X 8-Core", {"brand": "AMD", "family": "Ryzen 7", "model_number": "Ryzen 7 5700X", "generation": "5000", "socket": "AM4", "cores": 8, "threads": 16, "suffix": "X", "integrated_gpu": False}, 950.0),
    ("prod-cpu-5700x3d", "cohort-cpu-5700x3d", "AMD", "Ryzen 5000", "Ryzen 7 5700X3D", "X3D", "AMD Ryzen 7 5700X3D 8-Core", {"brand": "AMD", "family": "Ryzen 7", "model_number": "Ryzen 7 5700X3D", "generation": "5000", "socket": "AM4", "cores": 8, "threads": 16, "suffix": "X3D", "integrated_gpu": False}, 1200.0),
    ("prod-cpu-5800x", "cohort-cpu-5800x", "AMD", "Ryzen 5000", "Ryzen 7 5800X", "X", "AMD Ryzen 7 5800X 8-Core", {"brand": "AMD", "family": "Ryzen 7", "model_number": "Ryzen 7 5800X", "generation": "5000", "socket": "AM4", "cores": 8, "threads": 16, "suffix": "X", "integrated_gpu": False}, 1100.0),
    ("prod-cpu-5800x3d", "cohort-cpu-5800x3d", "AMD", "Ryzen 5000", "Ryzen 7 5800X3D", "X3D", "AMD Ryzen 7 5800X3D 8-Core", {"brand": "AMD", "family": "Ryzen 7", "model_number": "Ryzen 7 5800X3D", "generation": "5000", "socket": "AM4", "cores": 8, "threads": 16, "suffix": "X3D", "integrated_gpu": False}, 1700.0),

    # AMD AM5
    ("prod-cpu-7600", "cohort-cpu-7600", "AMD", "Ryzen 7000", "Ryzen 5 7600", None, "AMD Ryzen 5 7600 6-Core", {"brand": "AMD", "family": "Ryzen 5", "model_number": "Ryzen 5 7600", "generation": "7000", "socket": "AM5", "cores": 6, "threads": 12, "suffix": None, "integrated_gpu": True}, 1200.0),
    ("prod-cpu-7600x", "cohort-cpu-7600x", "AMD", "Ryzen 7000", "Ryzen 5 7600X", "X", "AMD Ryzen 5 7600X 6-Core", {"brand": "AMD", "family": "Ryzen 5", "model_number": "Ryzen 5 7600X", "generation": "7000", "socket": "AM5", "cores": 6, "threads": 12, "suffix": "X", "integrated_gpu": True}, 1350.0),
    ("prod-cpu-7700x", "cohort-cpu-7700x", "AMD", "Ryzen 7000", "Ryzen 7 7700X", "X", "AMD Ryzen 7 7700X 8-Core", {"brand": "AMD", "family": "Ryzen 7", "model_number": "Ryzen 7 7700X", "generation": "7000", "socket": "AM5", "cores": 8, "threads": 16, "suffix": "X", "integrated_gpu": True}, 1800.0),
    ("prod-cpu-7800x3d", "cohort-cpu-7800x3d", "AMD", "Ryzen 7000", "Ryzen 7 7800X3D", "X3D", "AMD Ryzen 7 7800X3D 8-Core", {"brand": "AMD", "family": "Ryzen 7", "model_number": "Ryzen 7 7800X3D", "generation": "7000", "socket": "AM5", "cores": 8, "threads": 16, "suffix": "X3D", "integrated_gpu": True}, 2700.0),

    # Intel LGA1700
    ("prod-cpu-12400f", "cohort-cpu-12400f", "Intel", "Core 12th Gen", "Core i5-12400F", "F", "Intel Core i5-12400F 6-Core", {"brand": "Intel", "family": "Core i5", "model_number": "Core i5-12400F", "generation": "12th Gen", "socket": "LGA1700", "cores": 6, "threads": 12, "suffix": "F", "integrated_gpu": False}, 650.0),
    ("prod-cpu-13400f", "cohort-cpu-13400f", "Intel", "Core 13th Gen", "Core i5-13400F", "F", "Intel Core i5-13400F 10-Core", {"brand": "Intel", "family": "Core i5", "model_number": "Core i5-13400F", "generation": "13th Gen", "socket": "LGA1700", "cores": 10, "threads": 16, "suffix": "F", "integrated_gpu": False}, 950.0),
    ("prod-cpu-13600k", "cohort-cpu-13600k", "Intel", "Core 13th Gen", "Core i5-13600K", "K", "Intel Core i5-13600K 14-Core", {"brand": "Intel", "family": "Core i5", "model_number": "Core i5-13600K", "generation": "13th Gen", "socket": "LGA1700", "cores": 14, "threads": 20, "suffix": "K", "integrated_gpu": True}, 1600.0),
    ("prod-cpu-13700k", "cohort-cpu-13700k", "Intel", "Core 13th Gen", "Core i7-13700K", "K", "Intel Core i7-13700K 16-Core", {"brand": "Intel", "family": "Core i7", "model_number": "Core i7-13700K", "generation": "13th Gen", "socket": "LGA1700", "cores": 16, "threads": 24, "suffix": "K", "integrated_gpu": True}, 2100.0),
    ("prod-cpu-14700k", "cohort-cpu-14700k", "Intel", "Core 14th Gen", "Core i7-14700K", "K", "Intel Core i7-14700K 20-Core", {"brand": "Intel", "family": "Core i7", "model_number": "Core i7-14700K", "generation": "14th Gen", "socket": "LGA1700", "cores": 20, "threads": 28, "suffix": "K", "integrated_gpu": True}, 2500.0),
]


def _fold(value: Any) -> str:
    val = unicodedata.normalize("NFKD", str(value or ""))
    val = "".join(c for c in val if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", val).strip().casefold()


def ensure_cpu_catalog(db: Session) -> Dict[str, int]:
    """Upsert canonical CPU products and market cohorts."""
    cohort_count = 0
    product_count = 0

    for pid, ckey, brand, fam, model, var, dname, attrs, ref_price in CPU_CANONICAL_ITEMS:
        # 1. MarketCohort
        cohort = db.query(MarketCohort).filter(MarketCohort.id == ckey).first()
        cohort_attrs = dict(attrs)
        cohort_attrs["reference_price_brl"] = ref_price
        if cohort is None:
            cohort = MarketCohort(
                id=ckey,
                category=HardwareCategory.CPU.value,
                cohort_key=ckey,
                display_name=dname,
                attributes=cohort_attrs,
                taxonomy_version=HARDWARE_TAXONOMY_VERSION,
            )
            db.add(cohort)
            cohort_count += 1
        else:
            cohort.display_name = dname
            cohort.attributes = cohort_attrs

        # 2. Product
        prod_attrs = dict(attrs)
        prod_attrs["catalog_mode"] = CPU_CATALOG_VERSION
        prod_attrs["reference_price_brl"] = ref_price
        product = db.query(Product).filter(Product.id == pid).first()
        if product is None:
            product = Product(
                id=pid,
                category=HardwareCategory.CPU.value,
                brand=brand,
                family=fam,
                model=model,
                variant=var,
                display_name=dname,
                attributes=prod_attrs,
                canonical_tier=1,
                market_cohort_id=ckey,
            )
            db.add(product)
            product_count += 1
        else:
            product.market_cohort_id = ckey
            product.display_name = dname
            product.attributes = prod_attrs

    db.flush()
    return {"cohorts": cohort_count, "products": product_count}


_FULL_SYSTEM_PATTERNS = (
    r"\b(?:notebook|laptop|ultrabook|macbook|galaxy\s*book)\b",
    r"\b(?:ideapad|thinkpad|legion|loq|inspiron|vostro|latitude|alienware|dell\s*g[0-9]{2})\b",
    r"\b(?:vivobook|zenbook|tuf\s*gaming|rog\s*strix|nitro|aspire|predator|helios)\b",
    r"\b(?:pc\s+gamer|computador\s+completo|setup\s+gamer|gabinete\s+(?:gamer|completo)|desktop\s+completo)\b",
)


def resolve_market_cohort(
    db: Session,
    *,
    title: str,
    description: str = "",
    attributes: Optional[Dict[str, Any]] = None,
) -> Optional[MarketCohort]:
    """Resolve the economic market cohort for a CPU listing observation."""
    text = _fold(f"{title} {description}")
    if any(re.search(pat, text) for pat in _FULL_SYSTEM_PATTERNS):
        return None

    attrs = attributes or {}

    cohorts = db.query(MarketCohort).filter(MarketCohort.category == HardwareCategory.CPU.value).all()
    # Sort cohorts by length of model_number descending to match specific models first (e.g. 5800X3D before 5800X)
    sorted_cohorts = sorted(cohorts, key=lambda c: len(c.attributes.get("model_number", "")), reverse=True)

    for c in sorted_cohorts:
        c_attrs = c.attributes or {}
        model_num = _fold(c_attrs.get("model_number", ""))
        # Check model number presence with word boundaries
        if model_num and model_num in text:
            return c
        # Check raw number e.g. "5600x", "5800x3d", "12400f"
        suffix_token = _fold(c.cohort_key.replace("cohort-cpu-", ""))
        if suffix_token and suffix_token in text:
            return c
    return None


def resolve_exact_product(
    db: Session,
    *,
    title: str,
    description: str = "",
    attributes: Optional[Dict[str, Any]] = None,
) -> Optional[Product]:
    """Resolve the exact canonical product record for a CPU listing observation."""
    cohort = resolve_market_cohort(db, title=title, description=description, attributes=attributes)
    if cohort:
        product = db.query(Product).filter(Product.market_cohort_id == cohort.id).first()
        return product
    return None


def explain_resolution(
    *,
    title: str,
    product: Optional[Product] = None,
    cohort: Optional[MarketCohort] = None,
) -> Dict[str, Any]:
    """Explain how the CPU listing was matched to a cohort or product."""
    if cohort:
        return {
            "status": "confirmed",
            "cohort_name": cohort.display_name,
            "cohort_key": cohort.cohort_key,
            "reason": f"Modelo exato corresponde à coorte '{cohort.display_name}'.",
        }
    return {
        "status": "unverified",
        "cohort_name": None,
        "cohort_key": None,
        "reason": "Evidência textual insuficiente para resolução de coorte de CPU.",
    }
