"""Brazil notebook catalogue import and deterministic listing identification.

The CSV is a product catalogue, not a marketplace feed.  It is therefore
used after marketplace retrieval to identify a concrete notebook model; it
never turns a catalogue row into a listing or into a confirmed opportunity.
"""

from __future__ import annotations

import csv
import os
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from packages.core.models import Product


NOTEBOOK_CATALOG_MODE = "notebook-brasil-v1"
CATALOG_SOURCE = "Notebook Dataset Brasil"
_DATASET_FILENAME = "Notebook Dataset Brasil - products.csv"


def _fold(value: Any) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in value if not unicodedata.combining(char)).casefold()


def _dataset_path() -> Path:
    configured = os.getenv("NOTEBOOK_CATALOG_DATASET")
    if configured:
        return Path(configured)
    root_path = Path(__file__).resolve().parents[2] / _DATASET_FILENAME
    if root_path.is_file():
        return root_path
    fixture_path = Path(__file__).resolve().parents[2] / "fixtures" / "product_knowledge" / "notebook_catalog.csv"
    if fixture_path.is_file():
        return fixture_path
    return root_path


def _catalog_product_id(source_id: str) -> str:
    return f"catalog-notebook-{source_id}"


def _as_float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    folded = _fold(value)
    if folded in {"true", "sim", "yes"}:
        return True
    if folded in {"false", "nao", "no"}:
        return False
    return None


def _unique(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        folded = _fold(text)
        if text and folded not in seen:
            seen.add(folded)
            result.append(text)
    return result


def _catalog_aliases(rows: list[dict[str, str]]) -> list[str]:
    values: list[str] = []
    for row in rows:
        values.extend(
            row.get(field, "")
            for field in (
                "product_id", "manufacturer_code", "sku", "mpn", "model",
                "full_name", "title_raw", "model_raw", "aliases",
            )
        )
        values.extend(part.strip() for part in row.get("aliases", "").split(";"))
    return _unique(values)


def _attributes(rows: list[dict[str, str]], aliases: list[str]) -> dict[str, Any]:
    row = rows[-1]
    prices = [price for price in (_as_float(item.get("price_brl")) for item in rows) if price is not None]
    return {
        "catalog_mode": NOTEBOOK_CATALOG_MODE,
        "catalog_source": CATALOG_SOURCE,
        "catalog_aliases": aliases,
        "reference_price_brl": min(prices) if prices else None,
        "reference_prices_brl": sorted(set(prices)),
        "cpu_brand": row.get("cpu_brand") or None,
        "cpu_model": row.get("cpu_model") or None,
        "cpu_generation": row.get("cpu_generation") or None,
        "ram_gb": _as_float(row.get("ram_gb")),
        "ram_type": row.get("ram_type") or None,
        "storage_gb": _as_float(row.get("storage_gb")),
        "storage_type": row.get("storage_type") or None,
        "gpu": row.get("gpu") or None,
        "screen_size_inches": _as_float(row.get("screen_size_inches")),
        "screen_resolution": row.get("screen_resolution") or None,
        "panel_type": row.get("screen_type") or None,
        "screen_ips": _as_bool(row.get("screen_ips")),
        "wifi_standard": row.get("wifi_standard") or None,
        "wifi_5ghz_or_better": _as_bool(row.get("wifi_5ghz_or_better")),
        "keyboard_abnt2": _as_bool(row.get("keyboard_abnt2")),
        "operating_system": row.get("operating_system") or None,
        "condition": row.get("condition") or None,
        "source_url": row.get("url") or row.get("source_url") or None,
        "screen_raw": row.get("screen_raw") or None,
        "title_raw": row.get("title_raw") or None,
        "keyboard_raw": row.get("keyboard_raw") or None,
    }


def ensure_notebook_catalog(db, *, dataset_path: Path | None = None) -> dict[str, int]:
    """Upsert the submitted catalogue and return its canonical product count."""
    path = dataset_path or _dataset_path()
    if not path.is_file():
        raise FileNotFoundError(f"Dataset de notebooks não encontrado: {path}")

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            source_id = str(row.get("product_id") or "").strip()
            if source_id:
                grouped[source_id].append(row)

    imported = 0
    for source_id, rows in grouped.items():
        row = rows[-1]
        aliases = _catalog_aliases(rows)
        product_id = _catalog_product_id(source_id)
        product = db.query(Product).filter(Product.id == product_id).first()
        values = {
            "category": "notebook",
            "brand": str(row.get("brand") or "Desconhecida").strip(),
            "family": str(row.get("family") or row.get("series") or "Notebook").strip(),
            "model": str(row.get("model") or source_id).strip(),
            "variant": str(row.get("manufacturer_code") or row.get("sku") or "").strip() or None,
            "display_name": str(row.get("full_name") or row.get("title_raw") or source_id).strip(),
            "attributes": _attributes(rows, aliases),
            "canonical_tier": 1,
        }
        if product is None:
            product = Product(id=product_id, **values)
            db.add(product)
        else:
            for field, value in values.items():
                setattr(product, field, value)
        imported += 1
    db.flush()
    return {"products": imported, "rows": sum(len(rows) for rows in grouped.values())}


def _is_stop_token(token: str) -> bool:
    patterns = [
        r"^(?:[0-9]{1,2}gb|[0-9]{1,2}g|ddr[345]|lpddr[45])$",
        r"^(?:[0-9]{2,4}gb|[0-9]tb|ssd|nvme|m2|emmc|hd|hdd)$",
        r"^(?:[0-9]{2,3}hz|[0-9]{2}(?:p|pol|\")|fhd|qhd|uhd|4k|ips|wva|oled|tn)$",
        r"^(?:win10|win11|w11|w10|w11p|w11h|[0-9]{1,2}th|[0-9]{1,2}a)$"
    ]
    return any(re.match(p, token) for p in patterns)


def _identifiers(product: Product) -> list[str]:
    attrs = product.attributes or {}
    values = [product.model, product.variant, *list(attrs.get("catalog_aliases") or [])]
    identifiers: list[str] = []
    for value in values:
        for token in re.findall(r"[a-z0-9][a-z0-9/-]{3,}", _fold(value)):
            # A model identifier must contain a digit. Plain words such as
            # "notebook" and "vision" cannot identify a catalogue product.
            if any(char.isdigit() for char in token) and not _is_stop_token(token):
                identifiers.append(token)
                
    if product.family:
        f = _fold(product.family)
        if f and f not in {"notebook", "laptop"} and len(f) >= 4:
            identifiers.append(f)
            
    return _unique(identifiers)


def find_catalog_notebook(db, *, title: str, description: str = "", attributes: dict[str, Any] | None = None) -> Product | None:
    """Return an unambiguous catalogue match from listing evidence only."""
    evidence = _fold(" ".join((title or "", description or "", str(attributes or {}))))
    if not evidence:
        return None
    products = db.query(Product).filter(
        Product.category == "notebook",
    ).all()
    candidates: list[tuple[int, Product]] = []
    for product in products:
        attrs = product.attributes or {}
        if attrs.get("catalog_mode") != NOTEBOOK_CATALOG_MODE:
            continue
        identifiers = _identifiers(product)
        matched = [token for token in identifiers if token in evidence]
        if not matched:
            continue
        # A brand mismatch is stronger negative evidence than a shared CPU or
        # storage token. The source title is still allowed to omit the brand.
        brand = _fold(product.brand)
        if brand and brand not in evidence and len(matched) == 1 and len(matched[0]) < 6:
            continue
        # Dar maior peso para SKU exato / código de fabricante e para tokens que contenham número de modelo
        score = 0
        for token in matched:
            token_score = len(token) * 10
            if any(c.isdigit() for c in token) and any(c.isalpha() for c in token):
                token_score += 20
            if product.variant and token == _fold(product.variant):
                token_score += 50
            if product.model and token in _fold(product.model):
                token_score += 30
            score += token_score
            
        # Bônus se a família do produto estiver explicitamente na evidência
        if product.family:
            family_folded = _fold(product.family)
            if family_folded and family_folded != "notebook":
                # family can be multiple words, check if it's in evidence
                if family_folded in evidence:
                    score += 100
            
        score += len(matched)
        candidates.append((score, product))
    if not candidates:
        return None
    
    candidates.sort(key=lambda item: (-item[0], item[1].display_name))
    
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        family_0 = _fold(candidates[0][1].family)
        family_1 = _fold(candidates[1][1].family)
        if family_0 and family_0 == family_1:
            return candidates[0][1]
        return None
        
    return candidates[0][1]


def resolve_exact_product(
    db,
    *,
    title: str,
    description: str = "",
    attributes: dict[str, Any] | None = None,
) -> Product | None:
    """Standard catalog interface: resolve exact notebook product."""
    return find_catalog_notebook(db, title=title, description=description, attributes=attributes)


def resolve_market_cohort(
    db,
    *,
    title: str,
    description: str = "",
    attributes: dict[str, Any] | None = None,
):
    """Standard catalog interface: resolve market cohort for notebook."""
    product = find_catalog_notebook(db, title=title, description=description, attributes=attributes)
    if product and product.market_cohort_id:
        from packages.core.models import MarketCohort
        return db.query(MarketCohort).filter(MarketCohort.id == product.market_cohort_id).first()
    return None


def explain_resolution(
    *,
    title: str,
    product: Product | None = None,
    cohort: Any = None,
) -> dict[str, Any]:
    """Standard catalog interface: explain resolution verdict."""
    if product:
        return {
            "status": "confirmed",
            "product_name": product.display_name,
            "product_id": product.id,
            "cohort_name": getattr(cohort, "display_name", None),
            "reason": f"Anúncio identificado no catálogo como '{product.display_name}'.",
        }
    return {
        "status": "unverified",
        "product_name": None,
        "product_id": None,
        "cohort_name": None,
        "reason": "Modelo exato do notebook não encontrado de forma unívoca no catálogo.",
    }


def is_free_model_notebook_plan(plan: Any) -> bool:
    """Whether a plan asks for notebooks and should cross-reference the Brazilian catalogue."""
    use_dataset = getattr(plan, "use_dataset_match", None)
    if use_dataset is None and isinstance(plan, dict):
        use_dataset = plan.get("use_dataset_match")
    if use_dataset is False:
        return False

    try:
        intent = plan.intent
        must = plan.must
    except AttributeError:
        intent = (plan or {}).get("intent", {}) if isinstance(plan, dict) else {}
        must = (plan or {}).get("must", []) if isinstance(plan, dict) else []
    category = getattr(intent, "category", None) if not isinstance(intent, dict) else intent.get("category")
    if _fold(category) != "notebook":
        return False
    if use_dataset is True:
        return True
    for field in ("models", "generations"):
        values = getattr(intent, field, None) if not isinstance(intent, dict) else intent.get(field)
        if values:
            return False
    for criterion in must or []:
        field = getattr(criterion, "field", None) if not isinstance(criterion, dict) else criterion.get("field")
        if _fold(field) in {"model", "generation"}:
            return False
    return True

