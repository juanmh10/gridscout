"""Analytical eligibility evaluation for hardware listings.

This is a pure deterministic policy deciding whether a listing observation is
clean and eligible to enter market cohort calculations, statistics, comparables,
and opportunities.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from packages.core.hardware_schemas import EligibilityEvaluationResult
from packages.core.hardware_taxonomy import (
    EXCLUSION_REASONS,
    ClassificationStatus,
    ExclusionCode,
    HardwareCategory,
    ItemForm,
    normalize_category,
    normalize_item_form,
)


COMPONENT_CATEGORIES = {
    HardwareCategory.GPU,
    HardwareCategory.CPU,
    HardwareCategory.RAM,
    HardwareCategory.SSD,
    HardwareCategory.MOTHERBOARD,
}

SYSTEM_CATEGORIES = {
    HardwareCategory.NOTEBOOK,
    HardwareCategory.DESKTOP,
}


def evaluate_analytics_eligibility(
    category: Any,
    item_form: Any,
    classification_status: Any,
    price: float | int | None,
    condition: Optional[str] = None,
    attributes: Optional[Dict[str, Any]] = None,
    *,
    target_category: Optional[Any] = None,
) -> EligibilityEvaluationResult:
    """Evaluate if an observation satisfies analytical criteria for inclusion.

    Pure function with zero side effects.
    """
    cat = normalize_category(category)
    form = normalize_item_form(item_form)
    attrs = attributes or {}
    codes: List[str] = []

    # 1. Classification confirmation requirement
    status_str = str(classification_status.value if isinstance(classification_status, ClassificationStatus) else classification_status or "").lower()
    if status_str == ClassificationStatus.LEGACY_UNVERIFIED.value:
        codes.append(ExclusionCode.LEGACY_WITHOUT_PROVENANCE.value)
    elif status_str != ClassificationStatus.CONFIRMED.value:
        codes.append(ExclusionCode.UNVERIFIED_EVIDENCE.value)

    # 2. Target category match (if cohort / scope target specified)
    if target_category is not None:
        target_cat = normalize_category(target_category)
        if cat != target_cat:
            codes.append(ExclusionCode.CATEGORY_MISMATCH.value)

    # 3. (Removed) Other category is now allowed unless target_category explicitly mismatches

    # 4. Price validity
    try:
        price_num = float(price) if price is not None else 0.0
    except (TypeError, ValueError):
        price_num = 0.0
    if price_num <= 0:
        codes.append(ExclusionCode.INVALID_PRICE.value)

    # 5. Condition / parts / defective
    cond_norm = str(condition or "").strip().lower()
    if cond_norm in {"for_parts", "pecas", "peças", "sucata", "defeito", "quebrado"}:
        codes.append(ExclusionCode.PARTS_OR_BROKEN.value)
    if form == ItemForm.PARTS:
        if ExclusionCode.PARTS_OR_BROKEN.value not in codes:
            codes.append(ExclusionCode.PARTS_OR_BROKEN.value)

    # 6. Item form checks by category type
    if cat in COMPONENT_CATEGORIES:
        if form == ItemForm.BUNDLE:
            codes.append(ExclusionCode.BUNDLE_NOT_STANDALONE.value)
        elif form == ItemForm.FULL_SYSTEM:
            codes.append(ExclusionCode.FULL_SYSTEM_NOT_COMPONENT.value)
        elif form == ItemForm.ACCESSORY:
            codes.append(ExclusionCode.BUNDLE_NOT_STANDALONE.value)
        elif form == ItemForm.UNKNOWN:
            codes.append(ExclusionCode.REQUIRED_ATTRIBUTE_UNKNOWN.value)
    elif cat in SYSTEM_CATEGORIES:
        if form == ItemForm.ACCESSORY:
            codes.append(ExclusionCode.BUNDLE_NOT_STANDALONE.value)
        elif form == ItemForm.UNKNOWN:
            codes.append(ExclusionCode.REQUIRED_ATTRIBUTE_UNKNOWN.value)

    # 7. Category-specific mandatory attribute checks for economic unit integrity
    if cat == HardwareCategory.RAM:
        # RAM must have ddr_standard/ram_generation and total_capacity_gb/capacity_gb
        has_ddr = bool(attrs.get("ddr_standard") or attrs.get("ram_generation"))
        has_cap = bool(attrs.get("total_capacity_gb") or attrs.get("capacity_gb"))
        if not has_ddr or not has_cap:
            if ExclusionCode.REQUIRED_ATTRIBUTE_UNKNOWN.value not in codes:
                codes.append(ExclusionCode.REQUIRED_ATTRIBUTE_UNKNOWN.value)
    elif cat == HardwareCategory.GPU:
        # GPU must have chip_model/chipset/model and vram_gb
        has_chip = bool(attrs.get("chip_model") or attrs.get("chipset") or attrs.get("model"))
        has_vram = bool(attrs.get("vram_gb") or attrs.get("vram"))
        if not has_chip:
            if ExclusionCode.REQUIRED_ATTRIBUTE_UNKNOWN.value not in codes:
                codes.append(ExclusionCode.REQUIRED_ATTRIBUTE_UNKNOWN.value)
        if not has_vram:
            if ExclusionCode.REQUIRED_ATTRIBUTE_UNKNOWN.value not in codes:
                codes.append(ExclusionCode.REQUIRED_ATTRIBUTE_UNKNOWN.value)
    elif cat == HardwareCategory.CPU:
        # CPU must have model_number or model
        if not attrs.get("model_number") and not attrs.get("model"):
            if ExclusionCode.REQUIRED_ATTRIBUTE_UNKNOWN.value not in codes:
                codes.append(ExclusionCode.REQUIRED_ATTRIBUTE_UNKNOWN.value)

    # Deduplicate codes while preserving order
    unique_codes = list(dict.fromkeys(codes))
    is_eligible = len(unique_codes) == 0
    reasons = [EXCLUSION_REASONS.get(code, code) for code in unique_codes]

    return EligibilityEvaluationResult(
        analytics_eligible=is_eligible,
        exclusion_codes=unique_codes,
        reasons=reasons,
    )
