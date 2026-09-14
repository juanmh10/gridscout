"""RAM canonical catalog and market cohort resolver.

Deterministic catalog for Desktop (UDIMM), Notebook (SODIMM), and Server (RDIMM ECC) RAM.
Provides resolve_exact_product, resolve_market_cohort, and explain_resolution.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from packages.core.hardware_taxonomy import HARDWARE_TAXONOMY_VERSION, HardwareCategory
from packages.core.models import MarketCohort, Product


RAM_CATALOG_VERSION = "ram-catalog-v1"

# Canonical RAM definitions: (product_id, cohort_key, brand, family, model, variant, display_name, attributes, reference_price)
RAM_CANONICAL_ITEMS = [
    # DDR4 Desktop UDIMM
    ("prod-ram-ddr4-8gb-3200-udimm", "cohort-ram-ddr4-8gb-3200-udimm", "Generic", "DDR4 UDIMM", "DDR4 8GB 3200MHz UDIMM", "8GB", "Memória RAM DDR4 8GB 3200MHz UDIMM (Desktop)", {"ddr_standard": "DDR4", "total_capacity_gb": 8, "form_factor": "UDIMM", "frequency_mhz": 3200, "ecc_support": False, "module_count": 1}, 120.0),
    ("prod-ram-ddr4-16gb-3200-udimm", "cohort-ram-ddr4-16gb-3200-udimm", "Generic", "DDR4 UDIMM", "DDR4 16GB 3200MHz UDIMM", "16GB", "Memória RAM DDR4 16GB 3200MHz UDIMM (Desktop)", {"ddr_standard": "DDR4", "total_capacity_gb": 16, "form_factor": "UDIMM", "frequency_mhz": 3200, "ecc_support": False, "module_count": 1}, 220.0),
    ("prod-ram-ddr4-32gb-3200-udimm", "cohort-ram-ddr4-32gb-3200-udimm", "Generic", "DDR4 UDIMM", "DDR4 32GB 3200MHz UDIMM", "32GB (2x16GB)", "Memória RAM DDR4 32GB (2x16GB) 3200MHz UDIMM", {"ddr_standard": "DDR4", "total_capacity_gb": 32, "form_factor": "UDIMM", "frequency_mhz": 3200, "ecc_support": False, "module_count": 2}, 420.0),

    # DDR4 Notebook SODIMM
    ("prod-ram-ddr4-8gb-3200-sodimm", "cohort-ram-ddr4-8gb-3200-sodimm", "Generic", "DDR4 SODIMM", "DDR4 8GB 3200MHz SODIMM", "8GB", "Memória RAM DDR4 8GB 3200MHz SODIMM (Notebook)", {"ddr_standard": "DDR4", "total_capacity_gb": 8, "form_factor": "SODIMM", "frequency_mhz": 3200, "ecc_support": False, "module_count": 1}, 110.0),
    ("prod-ram-ddr4-16gb-3200-sodimm", "cohort-ram-ddr4-16gb-3200-sodimm", "Generic", "DDR4 SODIMM", "DDR4 16GB 3200MHz SODIMM", "16GB", "Memória RAM DDR4 16GB 3200MHz SODIMM (Notebook)", {"ddr_standard": "DDR4", "total_capacity_gb": 16, "form_factor": "SODIMM", "frequency_mhz": 3200, "ecc_support": False, "module_count": 1}, 210.0),

    # DDR5 Desktop UDIMM
    ("prod-ram-ddr5-16gb-5600-udimm", "cohort-ram-ddr5-16gb-5600-udimm", "Generic", "DDR5 UDIMM", "DDR5 16GB 5600MHz UDIMM", "16GB", "Memória RAM DDR5 16GB 5600MHz UDIMM (Desktop)", {"ddr_standard": "DDR5", "total_capacity_gb": 16, "form_factor": "UDIMM", "frequency_mhz": 5600, "ecc_support": False, "module_count": 1}, 320.0),
    ("prod-ram-ddr5-32gb-6000-udimm", "cohort-ram-ddr5-32gb-6000-udimm", "Generic", "DDR5 UDIMM", "DDR5 32GB 6000MHz UDIMM", "32GB (2x16GB)", "Memória RAM DDR5 32GB (2x16GB) 6000MHz UDIMM (Desktop)", {"ddr_standard": "DDR5", "total_capacity_gb": 32, "form_factor": "UDIMM", "frequency_mhz": 6000, "ecc_support": False, "module_count": 2}, 680.0),

    # DDR5 Notebook SODIMM
    ("prod-ram-ddr5-16gb-4800-sodimm", "cohort-ram-ddr5-16gb-4800-sodimm", "Generic", "DDR5 SODIMM", "DDR5 16GB 4800MHz SODIMM", "16GB", "Memória RAM DDR5 16GB 4800MHz SODIMM (Notebook)", {"ddr_standard": "DDR5", "total_capacity_gb": 16, "form_factor": "SODIMM", "frequency_mhz": 4800, "ecc_support": False, "module_count": 1}, 300.0),

    # DDR4 Server ECC RDIMM
    ("prod-ram-ddr4-32gb-2666-rdimm", "cohort-ram-ddr4-32gb-2666-rdimm", "Generic", "DDR4 RDIMM", "DDR4 32GB 2666MHz ECC RDIMM", "32GB ECC", "Memória RAM DDR4 32GB 2666MHz ECC RDIMM (Servidor)", {"ddr_standard": "DDR4", "total_capacity_gb": 32, "form_factor": "RDIMM", "frequency_mhz": 2666, "ecc_support": True, "module_count": 1}, 280.0),
]


def _fold(value: Any) -> str:
    val = unicodedata.normalize("NFKD", str(value or ""))
    val = "".join(c for c in val if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", val).strip().casefold()


def ensure_ram_catalog(db: Session) -> Dict[str, int]:
    """Upsert canonical RAM products and market cohorts."""
    cohort_count = 0
    product_count = 0

    for pid, ckey, brand, fam, model, var, dname, attrs, ref_price in RAM_CANONICAL_ITEMS:
        # 1. MarketCohort
        cohort = db.query(MarketCohort).filter(MarketCohort.id == ckey).first()
        cohort_attrs = dict(attrs)
        cohort_attrs["reference_price_brl"] = ref_price
        if cohort is None:
            cohort = MarketCohort(
                id=ckey,
                category=HardwareCategory.RAM.value,
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
        prod_attrs["catalog_mode"] = RAM_CATALOG_VERSION
        prod_attrs["reference_price_brl"] = ref_price
        product = db.query(Product).filter(Product.id == pid).first()
        if product is None:
            product = Product(
                id=pid,
                category=HardwareCategory.RAM.value,
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
    """Resolve the economic market cohort for a RAM listing observation."""
    text = _fold(f"{title} {description}")

    # A complete notebook or full system is not a standalone RAM module
    is_standalone_module = bool(re.search(r"\b(?:pente|m[oó]dulo|mem[oó]ria\s+ram|memoria\s+ram|sodimm|udimm|rdimm)\b", text))
    if any(re.search(pat, text) for pat in _FULL_SYSTEM_PATTERNS) and not is_standalone_module:
        return None
    if any(k in text for k in ("vendo notebook", "notebook gamer", "notebook usado", "notebook seminovo", "notebook acer", "notebook dell", "notebook lenovo", "notebook samsung", "notebook asus", "notebook hp")):
        return None

    attrs = attributes or {}

    ddr = attrs.get("ddr_standard")
    if not ddr:
        m_ddr = re.search(r"\b(ddr[345])\b", text)
        ddr = m_ddr.group(1).upper() if m_ddr else None

    cap = attrs.get("total_capacity_gb")
    if not cap:
        m_cap = re.search(r"\b(\d{1,3})\s*(?:gb|g)\b", text)
        if m_cap:
            try:
                cap = float(m_cap.group(1))
            except ValueError:
                pass

    form_factor = attrs.get("form_factor")
    if not form_factor:
        if "sodimm" in text or "para notebook" in text or "notebook" in text:
            form_factor = "SODIMM"
        elif "rdimm" in text or "ecc reg" in text or "servidor" in text:
            form_factor = "RDIMM"
        else:
            form_factor = "UDIMM"

    if not ddr or not cap:
        return None

    cohorts = db.query(MarketCohort).filter(MarketCohort.category == HardwareCategory.RAM.value).all()
    for c in cohorts:
        c_attrs = c.attributes or {}
        if c_attrs.get("ddr_standard") == ddr and c_attrs.get("total_capacity_gb") == cap:
            if c_attrs.get("form_factor") == form_factor:
                return c

    # Fallback to UDIMM if matching ddr + cap found
    for c in cohorts:
        c_attrs = c.attributes or {}
        if c_attrs.get("ddr_standard") == ddr and c_attrs.get("total_capacity_gb") == cap:
            return c

    return None


def resolve_exact_product(
    db: Session,
    *,
    title: str,
    description: str = "",
    attributes: Optional[Dict[str, Any]] = None,
) -> Optional[Product]:
    """Resolve the exact canonical product record for a RAM listing observation."""
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
    """Explain how the RAM listing was matched to a cohort or product."""
    if cohort:
        return {
            "status": "confirmed",
            "cohort_name": cohort.display_name,
            "cohort_key": cohort.cohort_key,
            "reason": f"DDR, capacidade e formato correspondem à coorte '{cohort.display_name}'.",
        }
    return {
        "status": "unverified",
        "cohort_name": None,
        "cohort_key": None,
        "reason": "DDR ou capacidade não identificada com precisão.",
    }
