"""Pre-model scope guard for high-precision deterministic exclusions.

Rejects recommendations, incompatible item forms (e.g. full systems when looking
for components), and explicit cross-category conflicts before invoking any model.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Optional, Tuple

from packages.core.hardware_taxonomy import (
    ExclusionCode,
    HardwareCategory,
    ItemForm,
    normalize_category,
)


def _fold(text: Any) -> str:
    val = unicodedata.normalize("NFKD", str(text or ""))
    val = "".join(c for c in val if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", val).strip().casefold()


# High precision tokens indicating full systems
FULL_SYSTEM_PATTERNS = [
    r"\bpc\s+gamer\b",
    r"\bcomputador\s+(?:completo|gamer|de\s+mesa)\b",
    r"\bsetup\s+gamer\b",
    r"\bgabinete\s+(?:gamer|completo|montado)\b",
    r"\bdesktop\s+(?:gamer|completo)\b",
    r"\bnotebook\b",
    r"\blaptop\b",
    r"\bultrabook\b",
    r"\bmacbook\b",
    r"\bgalaxy\s*book\b",
    r"\b(?:ideapad|thinkpad|legion|loq|thinkbook)\b",
    r"\b(?:inspiron|vostro|latitude|alienware|dell\s*g[0-9]{2})\b",
    r"\b(?:vivobook|zenbook|tuf\s*gaming|rog\s*strix|rog\s*zephyrus)\b",
    r"\b(?:nitro\s*[0-9v]*|aspire|predator|helios|swift)\b",
    r"\b(?:pavilion|omen|victus|elitebook|probook|envy|spectre)\b",
    r"\b(?:positivo\s*(?:vision|motion|master)|vaio\s*f[eh][0-9]*|avell)\b",
]

# High precision tokens indicating kits / bundles
BUNDLE_PATTERNS = [
    r"\bkit\s+(?:upgrade|gamer|placa|processador|intel|amd|ryzen|core)\b",
    r"\bcombo\b",
    r"\bplaca\s*[- ]?m[aã]e\s*\+\s*(?:proc|processador|cpu|ram|memoria)\b",
    r"\b(?:ryzen|core\s*i[3579]|cpu)\s*\+\s*placa\s*[- ]?m[aã]e\b",
]

# High precision tokens indicating defective / parts
PARTS_PATTERNS = [
    r"\bpara\s+(?:conserto|pe[cç]as|retirada\s+de\s+pe[cç]as|reparo)\b",
    r"\bcom\s+defeito\b",
    r"\bn[aã]o\s+(?:liga|funciona|da\s+video|d[aá]\s+sinal)\b",
    r"\bquebrad[oa]\b",
    r"\bsucata\b",
    r"\bqueimad[oa]\b",
    r"\bartefatos?\b",
    r"\btela\s+trincada\b",
]

# Compatibility marker (e.g., "memoria para notebook" is RAM, not notebook)
ACCESSORY_FOR_PATTERNS = [
    r"\b(?:mem[oó]ria|pente|ram|sodimm|ssd|tela|bateria|carregador|fonte|teclado|carca[cç]a)\s+para\s+notebook\b",
    r"\b(?:cooler|suporte|cabo|adaptador)\s+para\b",
]


def pre_model_scope_guard(
    title: str,
    description: str = "",
    target_category: Optional[Any] = None,
    attributes: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Optional[str], Optional[ItemForm], Optional[HardwareCategory]]:
    """Evaluate candidate text against high-precision exclusions.

    Returns:
        (is_allowed, exclusion_reason, detected_item_form, detected_category)
    """
    full_text = _fold(f"{title} {description}")

    # 1. Defective / Parts check
    for pat in PARTS_PATTERNS:
        if re.search(pat, full_text):
            return False, ExclusionCode.PARTS_OR_BROKEN.value, ItemForm.PARTS, None

    # 2. Target category specific guard
    if target_category is not None:
        target_cat = normalize_category(target_category)

        # Component searches (GPU, CPU, RAM, SSD, Motherboard) must reject Full Systems and Desktops
        if target_cat in {HardwareCategory.GPU, HardwareCategory.CPU, HardwareCategory.RAM, HardwareCategory.SSD, HardwareCategory.MOTHERBOARD}:
            is_standalone_ram = bool(re.search(r"\b(?:pente|m[oó]dulo|mem[oó]ria\s+ram|memoria\s+ram|sodimm)\b", full_text)) and bool(re.search(r"\b(?:para\s+notebook|p/\s*notebook|sodimm)\b", full_text)) and not any(k in full_text for k in ("vendo notebook", "notebook gamer", "notebook usado", "notebook seminovo", "notebook acer", "notebook dell", "notebook lenovo", "notebook samsung", "notebook asus", "notebook hp"))
            is_standalone_ssd = bool(re.search(r"\b(?:ssd\s+nvme|ssd\s+sata|ssd\s+m\.2)\b", full_text)) and bool(re.search(r"\b(?:para\s+notebook|p/\s*notebook)\b", full_text)) and not any(k in full_text for k in ("vendo notebook", "notebook gamer", "notebook usado", "notebook seminovo", "notebook acer", "notebook dell", "notebook lenovo", "notebook samsung", "notebook asus", "notebook hp"))

            for pat in FULL_SYSTEM_PATTERNS:
                if re.search(pat, full_text):
                    if target_cat == HardwareCategory.RAM and is_standalone_ram:
                        continue
                    if target_cat == HardwareCategory.SSD and is_standalone_ssd:
                        continue
                    return False, ExclusionCode.FULL_SYSTEM_NOT_COMPONENT.value, ItemForm.FULL_SYSTEM, HardwareCategory.DESKTOP if "pc" in full_text else HardwareCategory.NOTEBOOK

            # Component searches looking for standalone items reject explicit bundles
            for pat in BUNDLE_PATTERNS:
                if re.search(pat, full_text):
                    return False, ExclusionCode.BUNDLE_NOT_STANDALONE.value, ItemForm.BUNDLE, None

        # Notebook searches must reject parts / accessories / SODIMM avulsa
        if target_cat == HardwareCategory.NOTEBOOK:
            if re.search(r"\b(?:pente|mem[oó]ria\s+sodimm|sodimm\s+avuls[oa]|carca[cç]a|teclado\s+avulso|carregador\s+apenas)\b", full_text) and not re.search(r"\bnotebook\s+completo\b", full_text):
                return False, ExclusionCode.CATEGORY_MISMATCH.value, ItemForm.ACCESSORY, HardwareCategory.RAM

    return True, None, None, None
