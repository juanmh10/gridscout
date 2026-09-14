"""Small, explicit local hardware taxonomy used by deterministic compilation.

The taxonomy is intentionally data-first.  It captures only mappings that are
safe to make locally and exposes ordering for ``equivalent_or_better`` instead
of pretending that a free-form model comparison is objective.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


TAXONOMY_VERSION = "hardware-taxonomy-v1"

TAXONOMY: dict[str, Any] = {
    "categories": {
        "notebook": ("notebook", "laptop", "ultrabook", "computador portátil", "computador portatil"),
        "gpu": ("gpu", "placa de vídeo", "placa de video", "placa gráfica", "placa grafica", "rtx", "gtx"),
        "cpu": ("cpu", "processador", "ryzen", "core i3", "core i5", "core i7", "core i9"),
        "ram": ("ram", "memória", "memoria", "memória ram", "memoria ram"),
        "ssd": ("ssd", "nvme", "disco sólido", "disco solido"),
        "motherboard": ("motherboard", "placa-mãe", "placa mãe", "placa mae"),
        "desktop": ("desktop", "computador", "pc gamer", "pc"),
        "other": ("outro", "outros", "other", "console", "videogame", "video game", "ps5", "ps4", "playstation", "xbox", "nintendo"),
    },
    "brands": ("Samsung", "Lenovo", "Dell", "Acer", "Apple", "ASUS", "AMD", "Intel", "NVIDIA", "Sony", "Microsoft", "Nintendo", "Logitech", "Razer"),
    "families": {
        "Galaxy Book": {
            "category": "notebook",
            "generations": ("1", "2", "3", "4"),
            "aliases": ("galaxy book", "galaxybook"),
        },
        "Ryzen 5": {
            "category": "cpu",
            "aliases": ("ryzen 5",),
            # Higher is better only within this explicitly defined family.
            "ordered_models": (
                "Ryzen 5 3600",
                "Ryzen 5 4500",
                "Ryzen 5 5500",
                "Ryzen 5 5600",
                "Ryzen 5 5600X",
                "Ryzen 5 7600",
                "Ryzen 5 7600X",
            ),
        },
    },
    "panel_types": ("IPS", "TN", "OLED"),
    "wifi_bands": ("2.4GHz", "5GHz", "6GHz"),
    "attribute_aliases": {
        "wifi": "wifi_bands",
        "wi-fi": "wifi_bands",
        "wireless": "wifi_bands",
        "tela": "panel_type",
        "painel": "panel_type",
    },
    "units": {
        "ram": "GB",
        "ssd": "GB",
        "storage": "GB",
    },
}
LOCAL_TAXONOMY_V1 = TAXONOMY


def fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(c for c in text if not unicodedata.combining(c)).casefold().strip()


def canonical_category(value: str | None) -> str | None:
    text = fold(value)
    # Exact category words win over incidental aliases (e.g. ``ram`` in a
    # notebook request containing "16GB RAM").
    for canonical in TAXONOMY["categories"]:
        if re.search(rf"\b{re.escape(canonical)}\b", text):
            return canonical
    for canonical, aliases in TAXONOMY["categories"].items():
        if any(alias in text for alias in sorted(aliases, key=len, reverse=True)):
            return canonical
    return None


def normalize_unit(value: str | None, *, kind: str = "storage") -> str | None:
    """Normalize RAM/SSD units to GB; return None for unknown units."""

    if value is None:
        return None
    text = fold(value).replace(" ", "")
    if text in {"gb", "gib", "g"}:
        return "GB"
    if text in {"tb", "tib", "t"}:
        return "GB"  # numeric conversion is handled by ``normalize_quantity``.
    return None


def normalize_quantity(value: Any, unit: str | None, *, kind: str = "storage") -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    unit_key = fold(unit).replace(" ", "")
    if unit_key in {"tb", "tib", "t"}:
        return number * 1024.0
    if unit_key in {"mb", "mib"}:
        return number / 1024.0
    return number


def canonical_panel(value: Any) -> str | None:
    text = fold(value)
    for panel in TAXONOMY["panel_types"]:
        if text == panel.casefold() or panel.casefold() in text:
            return panel
    return None


def canonical_family(value: Any) -> str | None:
    text = fold(value)
    for family, data in TAXONOMY["families"].items():
        if any(alias in text for alias in data.get("aliases", ())):
            return family
    return None


def family_order(family: str, model: str) -> int | None:
    data = TAXONOMY["families"].get(family)
    if not data:
        return None
    ordered = [fold(item) for item in data.get("ordered_models", ())]
    model_fold = fold(model)
    for idx, item in enumerate(ordered):
        if model_fold == item or model_fold in item or item in model_fold:
            return idx
    # Galaxy Book generations have an explicit sortable order too.
    if family == "Galaxy Book":
        match = re.search(r"(?:galaxy\s*book\s*)([1-4])", model_fold)
        if match:
            return int(match.group(1)) - 1
    return None


def equivalent_models(family: str, requested: str) -> list[str]:
    """Return requested model and explicitly ordered equivalents at/above it."""

    data = TAXONOMY["families"].get(family, {})
    ordered = list(data.get("ordered_models", ()))
    rank = family_order(family, requested)
    if rank is None:
        return [requested]
    return ordered[rank:]


def sort_equivalent_or_better(family: str, requested: str) -> list[str]:
    """Compatibility alias emphasizing the criterion operator semantics."""

    return equivalent_models(family, requested)


def normalize_memory(value: Any, unit: str | None = None) -> float | None:
    return normalize_quantity(value, unit, kind="ram")


def normalize_storage(value: Any, unit: str | None = None) -> float | None:
    return normalize_quantity(value, unit, kind="ssd")


def extract_numeric(text: str, pattern: str) -> tuple[float, str] | None:
    match = re.search(pattern, fold(text), flags=re.I)
    if not match:
        return None
    return float(match.group(1)), match.group(2).upper()
