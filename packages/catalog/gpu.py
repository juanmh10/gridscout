"""GPU canonical catalog and market cohort resolver.

Deterministic catalog for NVIDIA, AMD, and Intel GPUs.
Provides resolve_exact_product, resolve_market_cohort, and explain_resolution.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session

from packages.core.hardware_taxonomy import HARDWARE_TAXONOMY_VERSION, HardwareCategory
from packages.core.models import MarketCohort, Product


GPU_CATALOG_VERSION = "gpu-catalog-v1"

# Canonical GPU definitions: (product_id, cohort_key, brand, family, model, variant, display_name, attributes, reference_price)
GPU_CANONICAL_ITEMS = [
    # NVIDIA RTX 30 Series
    ("prod-gpu-rtx3060-12g", "cohort-gpu-rtx3060-12g", "NVIDIA", "GeForce RTX 30 Series", "RTX 3060", "12GB", "NVIDIA GeForce RTX 3060 12GB", {"chip_brand": "NVIDIA", "chip_model": "RTX 3060", "vram_gb": 12, "memory_type": "GDDR6", "bus_width_bits": 192}, 1500.0),
    ("prod-gpu-rtx3060-8g", "cohort-gpu-rtx3060-8g", "NVIDIA", "GeForce RTX 30 Series", "RTX 3060", "8GB", "NVIDIA GeForce RTX 3060 8GB", {"chip_brand": "NVIDIA", "chip_model": "RTX 3060", "vram_gb": 8, "memory_type": "GDDR6", "bus_width_bits": 128}, 1300.0),
    ("prod-gpu-rtx3060ti", "cohort-gpu-rtx3060ti", "NVIDIA", "GeForce RTX 30 Series", "RTX 3060 Ti", "8GB", "NVIDIA GeForce RTX 3060 Ti 8GB", {"chip_brand": "NVIDIA", "chip_model": "RTX 3060 Ti", "vram_gb": 8, "memory_type": "GDDR6", "bus_width_bits": 256}, 1750.0),
    ("prod-gpu-rtx3070", "cohort-gpu-rtx3070", "NVIDIA", "GeForce RTX 30 Series", "RTX 3070", "8GB", "NVIDIA GeForce RTX 3070 8GB", {"chip_brand": "NVIDIA", "chip_model": "RTX 3070", "vram_gb": 8, "memory_type": "GDDR6", "bus_width_bits": 256}, 2000.0),
    ("prod-gpu-rtx3070ti", "cohort-gpu-rtx3070ti", "NVIDIA", "GeForce RTX 30 Series", "RTX 3070 Ti", "8GB", "NVIDIA GeForce RTX 3070 Ti 8GB", {"chip_brand": "NVIDIA", "chip_model": "RTX 3070 Ti", "vram_gb": 8, "memory_type": "GDDR6X", "bus_width_bits": 256}, 2300.0),
    ("prod-gpu-rtx3080-10g", "cohort-gpu-rtx3080-10g", "NVIDIA", "GeForce RTX 30 Series", "RTX 3080", "10GB", "NVIDIA GeForce RTX 3080 10GB", {"chip_brand": "NVIDIA", "chip_model": "RTX 3080", "vram_gb": 10, "memory_type": "GDDR6X", "bus_width_bits": 320}, 2750.0),
    ("prod-gpu-rtx3080-12g", "cohort-gpu-rtx3080-12g", "NVIDIA", "GeForce RTX 30 Series", "RTX 3080", "12GB", "NVIDIA GeForce RTX 3080 12GB", {"chip_brand": "NVIDIA", "chip_model": "RTX 3080", "vram_gb": 12, "memory_type": "GDDR6X", "bus_width_bits": 384}, 3000.0),
    ("prod-gpu-rtx3080ti", "cohort-gpu-rtx3080ti", "NVIDIA", "GeForce RTX 30 Series", "RTX 3080 Ti", "12GB", "NVIDIA GeForce RTX 3080 Ti 12GB", {"chip_brand": "NVIDIA", "chip_model": "RTX 3080 Ti", "vram_gb": 12, "memory_type": "GDDR6X", "bus_width_bits": 384}, 3300.0),
    ("prod-gpu-rtx3090", "cohort-gpu-rtx3090", "NVIDIA", "GeForce RTX 30 Series", "RTX 3090", "24GB", "NVIDIA GeForce RTX 3090 24GB", {"chip_brand": "NVIDIA", "chip_model": "RTX 3090", "vram_gb": 24, "memory_type": "GDDR6X", "bus_width_bits": 384}, 4200.0),

    # NVIDIA RTX 40 Series
    ("prod-gpu-rtx4060-8g", "cohort-gpu-rtx4060-8g", "NVIDIA", "GeForce RTX 40 Series", "RTX 4060", "8GB", "NVIDIA GeForce RTX 4060 8GB", {"chip_brand": "NVIDIA", "chip_model": "RTX 4060", "vram_gb": 8, "memory_type": "GDDR6", "bus_width_bits": 128}, 1800.0),
    ("prod-gpu-rtx4060ti-8g", "cohort-gpu-rtx4060ti-8g", "NVIDIA", "GeForce RTX 40 Series", "RTX 4060 Ti", "8GB", "NVIDIA GeForce RTX 4060 Ti 8GB", {"chip_brand": "NVIDIA", "chip_model": "RTX 4060 Ti", "vram_gb": 8, "memory_type": "GDDR6", "bus_width_bits": 128}, 2300.0),
    ("prod-gpu-rtx4060ti-16g", "cohort-gpu-rtx4060ti-16g", "NVIDIA", "GeForce RTX 40 Series", "RTX 4060 Ti", "16GB", "NVIDIA GeForce RTX 4060 Ti 16GB", {"chip_brand": "NVIDIA", "chip_model": "RTX 4060 Ti", "vram_gb": 16, "memory_type": "GDDR6", "bus_width_bits": 128}, 2700.0),
    ("prod-gpu-rtx4070-12g", "cohort-gpu-rtx4070-12g", "NVIDIA", "GeForce RTX 40 Series", "RTX 4070", "12GB", "NVIDIA GeForce RTX 4070 12GB", {"chip_brand": "NVIDIA", "chip_model": "RTX 4070", "vram_gb": 12, "memory_type": "GDDR6X", "bus_width_bits": 192}, 3600.0),
    ("prod-gpu-rtx4070super", "cohort-gpu-rtx4070super", "NVIDIA", "GeForce RTX 40 Series", "RTX 4070 SUPER", "12GB", "NVIDIA GeForce RTX 4070 SUPER 12GB", {"chip_brand": "NVIDIA", "chip_model": "RTX 4070 SUPER", "vram_gb": 12, "memory_type": "GDDR6X", "bus_width_bits": 192}, 4100.0),
    ("prod-gpu-rtx4070ti", "cohort-gpu-rtx4070ti", "NVIDIA", "GeForce RTX 40 Series", "RTX 4070 Ti", "12GB", "NVIDIA GeForce RTX 4070 Ti 12GB", {"chip_brand": "NVIDIA", "chip_model": "RTX 4070 Ti", "vram_gb": 12, "memory_type": "GDDR6X", "bus_width_bits": 192}, 4600.0),
    ("prod-gpu-rtx4080", "cohort-gpu-rtx4080", "NVIDIA", "GeForce RTX 40 Series", "RTX 4080", "16GB", "NVIDIA GeForce RTX 4080 16GB", {"chip_brand": "NVIDIA", "chip_model": "RTX 4080", "vram_gb": 16, "memory_type": "GDDR6X", "bus_width_bits": 256}, 6200.0),

    # AMD Radeon RX 6000 Series
    ("prod-gpu-rx6600", "cohort-gpu-rx6600", "AMD", "Radeon RX 6000", "RX 6600", "8GB", "AMD Radeon RX 6600 8GB", {"chip_brand": "AMD", "chip_model": "RX 6600", "vram_gb": 8, "memory_type": "GDDR6", "bus_width_bits": 128}, 1200.0),
    ("prod-gpu-rx6600xt", "cohort-gpu-rx6600xt", "AMD", "Radeon RX 6000", "RX 6600 XT", "8GB", "AMD Radeon RX 6600 XT 8GB", {"chip_brand": "AMD", "chip_model": "RX 6600 XT", "vram_gb": 8, "memory_type": "GDDR6", "bus_width_bits": 128}, 1350.0),
    ("prod-gpu-rx6700xt", "cohort-gpu-rx6700xt", "AMD", "Radeon RX 6000", "RX 6700 XT", "12GB", "AMD Radeon RX 6700 XT 12GB", {"chip_brand": "AMD", "chip_model": "RX 6700 XT", "vram_gb": 12, "memory_type": "GDDR6", "bus_width_bits": 192}, 1800.0),
    ("prod-gpu-rx6800xt", "cohort-gpu-rx6800xt", "AMD", "Radeon RX 6000", "RX 6800 XT", "16GB", "AMD Radeon RX 6800 XT 16GB", {"chip_brand": "AMD", "chip_model": "RX 6800 XT", "vram_gb": 16, "memory_type": "GDDR6", "bus_width_bits": 256}, 2600.0),

    # AMD Radeon RX 7000 Series
    ("prod-gpu-rx7600", "cohort-gpu-rx7600", "AMD", "Radeon RX 7000", "RX 7600", "8GB", "AMD Radeon RX 7600 8GB", {"chip_brand": "AMD", "chip_model": "RX 7600", "vram_gb": 8, "memory_type": "GDDR6", "bus_width_bits": 128}, 1600.0),
    ("prod-gpu-rx7700xt", "cohort-gpu-rx7700xt", "AMD", "Radeon RX 7000", "RX 7700 XT", "12GB", "AMD Radeon RX 7700 XT 12GB", {"chip_brand": "AMD", "chip_model": "RX 7700 XT", "vram_gb": 12, "memory_type": "GDDR6", "bus_width_bits": 192}, 2900.0),
    ("prod-gpu-rx7800xt", "cohort-gpu-rx7800xt", "AMD", "Radeon RX 7000", "RX 7800 XT", "16GB", "AMD Radeon RX 7800 XT 16GB", {"chip_brand": "AMD", "chip_model": "RX 7800 XT", "vram_gb": 16, "memory_type": "GDDR6", "bus_width_bits": 256}, 3600.0),
]


def _fold(value: Any) -> str:
    val = unicodedata.normalize("NFKD", str(value or ""))
    val = "".join(c for c in val if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", val).strip().casefold()


def ensure_gpu_catalog(db: Session) -> Dict[str, int]:
    """Upsert canonical GPU products and market cohorts."""
    cohort_count = 0
    product_count = 0
    now = sa_now = None

    for pid, ckey, brand, fam, model, var, dname, attrs, ref_price in GPU_CANONICAL_ITEMS:
        # 1. MarketCohort
        cohort = db.query(MarketCohort).filter(MarketCohort.id == ckey).first()
        cohort_attrs = dict(attrs)
        cohort_attrs["reference_price_brl"] = ref_price
        if cohort is None:
            cohort = MarketCohort(
                id=ckey,
                category=HardwareCategory.GPU.value,
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
        prod_attrs["catalog_mode"] = GPU_CATALOG_VERSION
        prod_attrs["reference_price_brl"] = ref_price
        product = db.query(Product).filter(Product.id == pid).first()
        if product is None:
            product = Product(
                id=pid,
                category=HardwareCategory.GPU.value,
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
    """Resolve the economic market cohort for a GPU listing observation."""
    text = _fold(f"{title} {description}")
    if any(re.search(pat, text) for pat in _FULL_SYSTEM_PATTERNS):
        return None

    attrs = attributes or {}
    chip_model = attrs.get("chip_model") or ""
    vram_gb = attrs.get("vram_gb")

    cohorts = db.query(MarketCohort).filter(MarketCohort.category == HardwareCategory.GPU.value).all()
    for c in cohorts:
        c_attrs = c.attributes or {}
        target_chip = _fold(c_attrs.get("chip_model", ""))
        target_vram = c_attrs.get("vram_gb")

        if target_chip and target_chip in text:
            # If cohort specifies VRAM variant (e.g. RTX 3060 12GB vs 8GB, RTX 3080 10GB vs 12GB)
            if target_vram:
                vram_str = f"{int(target_vram)}gb"
                vram_alt = f"{int(target_vram)} gb"
                if vram_str in text or vram_alt in text or vram_gb == target_vram:
                    return c
                # If neither 8gb nor 12gb is specified in text, return default primary variant
                if "3060" in target_chip and "12gb" in c.cohort_key:
                    return c
                if "3080" in target_chip and "10gb" in c.cohort_key:
                    return c
            else:
                return c
    return None


def resolve_exact_product(
    db: Session,
    *,
    title: str,
    description: str = "",
    attributes: Optional[Dict[str, Any]] = None,
) -> Optional[Product]:
    """Resolve the exact canonical product record for a GPU listing observation."""
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
    """Explain how the GPU listing was matched to a cohort or product."""
    if cohort:
        return {
            "status": "confirmed",
            "cohort_name": cohort.display_name,
            "cohort_key": cohort.cohort_key,
            "reason": f"Chip e especificações correspondem à coorte '{cohort.display_name}'.",
        }
    return {
        "status": "unverified",
        "cohort_name": None,
        "cohort_key": None,
        "reason": "Evidência textual insuficiente para resolução de coorte de GPU.",
    }
