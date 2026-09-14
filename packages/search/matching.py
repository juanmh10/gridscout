"""Typed matching and deterministic scoring for search candidates."""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Optional

from .models import CriterionOperator, SearchCandidate, SearchCriterionV1, SearchPlanV1
from .taxonomy import canonical_panel, equivalent_models, family_order, fold, normalize_quantity


class CandidateMatch:
    """Plain result object kept JSON-friendly for service adapters."""

    def __init__(self, *, status: str, must: list[dict[str, Any]], should: list[dict[str, Any]], must_not: list[dict[str, Any]], objective_score: float, request_match_score: float, affinity: float, final_score: float | None = None, ranking_score: float | None = None, personalized_score: float | None = None, reasons: list[str] | None = None):
        self.status = status
        self.must = must
        self.should = should
        self.must_not = must_not
        self.objective_score = round(max(0.0, min(100.0, objective_score)), 4)
        self.request_match_score = round(max(0.0, min(100.0, request_match_score)), 4)
        self.preference_affinity = round(max(-10.0, min(10.0, affinity)), 4)
        # ``affinity`` is retained as a compatibility field; the canonical
        # name is preference_affinity in the search contract.
        self.affinity = self.preference_affinity
        # final_score is the objective score, deliberately untouched by
        # request matching or personalization.  personalized_score is the
        # only score used for personalized ordering.
        self.final_score = self.objective_score
        self.personalized_score = round(
            max(0.0, min(100.0, 0.6 * self.objective_score + 0.3 * self.request_match_score + self.preference_affinity)),
            4,
        ) if personalized_score is None else round(max(0.0, min(100.0, personalized_score)), 4)
        self.ranking_score = self.personalized_score
        self.reasons = reasons or []

    def model_dump(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _candidate_data(candidate: Any) -> dict[str, Any]:
    if hasattr(candidate, "model_dump"):
        data = candidate.model_dump()
    elif isinstance(candidate, dict):
        data = dict(candidate)
    else:
        data = {key: getattr(candidate, key) for key in dir(candidate) if not key.startswith("_") and not callable(getattr(candidate, key))}
    attrs = dict(data.get("attributes") or {})
    data.update(attrs)
    return data


def _value_for(candidate: Any, field: str) -> Any:
    data = _candidate_data(candidate)
    normalized = field.casefold().replace("-", "_")
    aliases = {
        "ram": "ram_gb",
        "memory": "ram_gb",
        "storage": "ssd_gb",
        "ssd": "ssd_gb",
        "panel": "panel_type",
        "screen": "panel_type",
        "brand": "brand",
        "model": "model",
        "family": "family",
        "generation": "generation",
        "price": "price",
        "location": "location",
        "condition": "condition",
        "category": "category",
        "wifi": "wifi_bands",
        "wifi_band": "wifi_bands",
        "wireless": "wifi_bands",
    }
    key = aliases.get(normalized, normalized)
    if key in data and data[key] not in (None, ""):
        return data[key]
    attribute_aliases = {
        "panel_type": ("panel", "screen", "display", "display_type", "tela", "painel"),
        "wifi_bands": ("wifi", "wi-fi", "wireless", "wifi_band", "wifi_bands", "wireless_standard"),
        "ram_gb": ("ram", "memory", "memory_gb"),
        "ssd_gb": ("ssd", "storage", "storage_gb", "disk_gb"),
    }
    for alias in attribute_aliases.get(key, ()):
        if alias in data and data[alias] not in (None, ""):
            return data[alias]
    title = str(data.get("title") or "")
    if key == "brand":
        for brand in ("Samsung", "Lenovo", "Dell", "Acer", "Apple", "ASUS", "AMD", "Intel", "NVIDIA"):
            if fold(brand) in fold(title):
                return brand
    if key == "category":
        text = fold(title)
        
        # Hard isolation: explicit motherboards, desktops or barebones are 'other' or 'desktop'
        if any(token in text for token in ("placa mae", "placa-mae", "motherboard", "desktop", "gabinete", "computador", "cooler")):
            return "other"
            
        is_nb = any(token in text for token in (
            "notebook", "laptop", "ultrabook", "netbook", "chromebook", "macbook",
            "galaxy book", "galaxybook", "ideapad", "thinkpad", "legion", "loq",
            "inspiron", "vostro", "latitude", "alienware", "dell g15", "dell g3", "dell g5",
            "nitro", "aspire", "predator", "helios", "vivobook", "zenbook", "tuf gaming",
            "pavilion", "omen", "victus", "positivo vision", "vaio fe", "avell",
        ))
        is_ram_sodimm = any(token in text for token in ("sodimm", "pente de memoria", "modulo de memoria"))
        if is_nb and not is_ram_sodimm:
            return "notebook"
                
        if any(token in text for token in ("placa de video", "placa de vídeo", "gpu", "rtx ", "gtx ", "rx 6", "rx 7")) and not is_nb:
            return "gpu"
            
        if any(token in text for token in ("processador", "ryzen", "core i3", "core i5", "core i7", "core i9", "pentium", "xeon")) and not is_nb:
            return "cpu"
            
        if any(token in text for token in ("memoria ram", "memória ram", "pente de memoria", "pente de memória", "sodimm", "udimm", "ddr4 8gb", "ddr4 16gb", "ddr5 16gb", "ddr5 32gb")) and not is_nb:
            return "ram"

        if any(token in text for token in ("ssd nvme", "ssd sata", "ssd m.2", "ssd kingston", "ssd sandisk", "ssd crucial", "ssd samsung")) and not is_nb:
            return "ssd"
    if key == "family":
        text = fold(title)
        if "galaxy book" in text or "galaxybook" in text:
            return "Galaxy Book"
        if "ryzen 5" in text:
            return "Ryzen 5"
    if key == "generation":
        match = re.search(r"galaxy\s*book\s*([1-4])", fold(title))
        if match:
            return match.group(1)
    if key == "model":
        text = title
        for pattern in (r"(Ryzen\s+[3579]\s+[0-9]+[A-Za-z0-9-]*)", r"(RTX\s*[0-9]+)", r"(GTX\s*[0-9]+)", r"(Galaxy\s*Book\s*[1-4])", r"(M[123])"):
            match = re.search(pattern, text, flags=re.I)
            if match:
                return match.group(1)
    if key == "panel_type":
        for value in ("IPS", "TN", "OLED"):
            if re.search(rf"\b{value}\b", title, flags=re.I):
                return value
    if key == "wifi_bands":
        bands: list[str] = []
        for value, pattern in (("5GHz", r"(?:wi\s*[- ]?fi|wifi|wireless)?[^\d]{0,8}5\s*(?:ghz|g)"), ("6GHz", r"(?:wi\s*[- ]?fi|wifi|wireless)?[^\d]{0,8}6\s*(?:ghz|g)"), ("2.4GHz", r"(?:wi\s*[- ]?fi|wifi|wireless)?[^\d]{0,8}2[,.]?4\s*(?:ghz|g)")):
            if re.search(pattern, fold(title), flags=re.I):
                bands.append(value)
        return bands or None
    if key in {"ram_gb", "ssd_gb"}:
        label = "ram" if key == "ram_gb" else "ssd"
        match = re.search(rf"(\d+(?:[,.]\d+)?)\s*(gb|tb)\s*{label}", fold(title), flags=re.I)
        if not match:
            match = re.search(rf"\b{label}\s*(\d+(?:[,.]\d+)?)\s*(gb|tb)", fold(title), flags=re.I)
        if match:
            return normalize_quantity(match.group(1).replace(",", "."), match.group(2))
    return None


def _numeric(value: Any, unit: str | None = None) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?[\d]+(?:[,.]\d+)?", str(value))
    if not match:
        return None
    number = float(match.group(0).replace(",", "."))
    return normalize_quantity(number, unit)


def _eq(actual: Any, expected: Any) -> bool | None:
    if actual is None:
        return None
    if isinstance(actual, (list, tuple, set)):
        if isinstance(expected, (list, tuple, set)):
            return any(_eq(item, wanted) is True for item in actual for wanted in expected)
        return any(_eq(item, expected) is True for item in actual)
    if isinstance(expected, (list, tuple, set)):
        return any(_eq(actual, item) is True for item in expected)
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(float(actual), float(expected), rel_tol=0.001, abs_tol=0.001)
    actual_fold, expected_fold = fold(actual), fold(expected)
    condition_groups = {
        "new": {"new", "novo", "novos", "nova"},
        "used": {"used", "usado", "usados", "usada", "good", "like_new", "seminovo", "semi-novo"},
        "refurbished": {"refurbished", "recondicionado", "recondicionada"},
    }
    for canonical, aliases in condition_groups.items():
        if actual_fold in aliases:
            actual_fold = canonical
        if expected_fold in aliases:
            expected_fold = canonical
    if not actual_fold or not expected_fold:
        return None
    return actual_fold == expected_fold or expected_fold in actual_fold


def evaluate_criterion(candidate: Any, criterion: SearchCriterionV1) -> bool | None:
    actual = _value_for(candidate, criterion.field)
    expected = criterion.value
    op = criterion.operator
    if op == CriterionOperator.EQ:
        return _eq(actual, expected)
    if op == CriterionOperator.IN:
        if actual is None:
            return None
        return any(_eq(actual, item) is True for item in expected)
    if op in {CriterionOperator.GTE, CriterionOperator.LTE}:
        actual_n = _numeric(actual, criterion.unit)
        expected_n = _numeric(expected, criterion.unit)
        if actual_n is None or expected_n is None:
            return None
        return actual_n >= expected_n if op == CriterionOperator.GTE else actual_n <= expected_n
    if op == CriterionOperator.RANGE:
        if isinstance(expected, dict):
            low, high = expected.get("min"), expected.get("max")
        else:
            low, high = expected[0], expected[1]
        actual_n = _numeric(actual, criterion.unit)
        low_n, high_n = _numeric(low, criterion.unit), _numeric(high, criterion.unit)
        if actual_n is None or low_n is None or high_n is None:
            return None
        return low_n <= actual_n <= high_n
    if op == CriterionOperator.EQUIVALENT_OR_BETTER:
        expected_fold = fold(expected)
        actual_fold = fold(actual)
        family = "Ryzen 5" if "ryzen 5" in expected_fold or "ryzen 5" in actual_fold else "Galaxy Book" if "galaxy book" in expected_fold else ""
        if not family or actual is None:
            return None
        if family == "Ryzen 5" and expected_fold == "ryzen 5":
            # Tier-level equivalence is explicit and conservative: Ryzen 5
            # candidates satisfy the Ryzen 5 anchor; other Ryzen tiers are not
            # silently treated as equivalent.
            actual_family = _value_for(candidate, "family")
            if actual_family is None:
                return None
            return _eq(actual_family, "Ryzen 5")
        requested_rank = family_order(family, str(expected))
        actual_rank = family_order(family, str(actual))
        if requested_rank is None or actual_rank is None:
            return _eq(actual, expected)
        return actual_rank >= requested_rank
    return None


def _criterion_result(candidate: Any, criterion: SearchCriterionV1) -> dict[str, Any]:
    result = evaluate_criterion(candidate, criterion)
    actual = _value_for(candidate, criterion.field)
    
    if result is None and criterion.field == "panel_type" and str(criterion.value).upper() == "IPS":
        data = _candidate_data(candidate)
        if data.get("screen_ips") is True or data.get("panel_type") == "IPS":
            result = True
            actual = "IPS"
            
    return {
        "field": criterion.field,
        "operator": criterion.operator.value,
        "value": criterion.value,
        "unit": criterion.unit,
        "origin": criterion.origin,
        "matched": result,
        "actual": actual,
    }


def _affinity(candidate: Any, profile: Optional[dict[str, Any]]) -> float:
    if not profile:
        return 0.0
    data = _candidate_data(candidate)
    score = 0.0
    preferred_brands = {fold(value) for value in profile.get("preferred_brands", [])}
    brand = fold(_value_for(data, "brand"))
    if preferred_brands and brand:
        score += 5.0 if brand in preferred_brands else -2.0
    categories = profile.get("category_expertise", {}) or {}
    category = fold(_value_for(data, "category"))
    try:
        score += max(-5.0, min(5.0, float(categories.get(category, 0.0)) * 5.0))
    except (TypeError, ValueError):
        pass
    return max(-10.0, min(10.0, score))


def _objective(candidate: Any, plan: SearchPlanV1) -> float:
    data = _candidate_data(candidate)
    explicit = data.get("objective_score")
    if explicit is not None:
        try:
            return max(0.0, min(100.0, float(explicit)))
        except (TypeError, ValueError):
            pass
    price = _numeric(data.get("price"))
    budget_max = plan.intent.budget.max if plan.intent.budget else None
    if price is not None and budget_max and budget_max > 0:
        # Lower purchase price is the only objective available before market
        # statistics are loaded.  Never penalize a candidate beyond 0.
        return max(0.0, min(100.0, 100.0 * (1.0 - price / budget_max)))
    return 50.0


def evaluate_candidate(candidate: Any, plan: SearchPlanV1, *, affinity_profile: Optional[dict[str, Any]] = None) -> CandidateMatch:
    must = [_criterion_result(candidate, item) for item in plan.must]
    should = [_criterion_result(candidate, item) for item in plan.should]
    must_not = [_criterion_result(candidate, item) for item in plan.must_not]
    
    intent_category = plan.intent.category
    candidate_category = _value_for(candidate, "category")
    category_mismatch = intent_category and candidate_category and intent_category != candidate_category
    
    hard_violation = any(item["matched"] is False for item in must)
    excluded = any(item["matched"] is True for item in must_not)
    unknown_must = any(item["matched"] is None for item in must)
    
    if category_mismatch or hard_violation or excluded:
        status = "rejected"
    elif unknown_must:
        # A hard requirement with unknown evidence must never be confirmed.
        status = "unverified"
    else:
        status = "confirmed"
    known_should = [item["matched"] for item in should if item["matched"] is not None]
    should_ratio = sum(value is True for value in known_should) / len(known_should) if known_should else 0.0
    # Unknown hard evidence contributes neither a match nor a violation to
    # filtering, but it must lower the request score and remain unconfirmed.
    request_must_ratio = sum(item["matched"] is True for item in must) / len(must) if must else 1.0
    request_ratio = request_must_ratio * 0.7 + should_ratio * 0.3
    request_match_score = 100.0 * request_ratio
    objective_score = _objective(candidate, plan)
    affinity = _affinity(candidate, affinity_profile)
    reasons = []
    if status == "unverified":
        reasons.append("Há requisito obrigatório sem evidência suficiente; não confirmado.")
    if status == "rejected":
        if category_mismatch:
            reasons.append(f"Descarte determinístico: Busca por '{intent_category}' mas o anúncio é da categoria '{candidate_category}'.")
        if hard_violation or excluded:
            reasons.append("Pelo menos um requisito obrigatório foi violado ou uma exclusão foi encontrada.")
    return CandidateMatch(
        status=status,
        must=must,
        should=should,
        must_not=must_not,
        objective_score=objective_score,
        request_match_score=request_match_score,
        affinity=affinity,
        final_score=objective_score,
        personalized_score=max(0.0, min(100.0, 0.6 * objective_score + 0.3 * request_match_score + affinity)),
        ranking_score=max(0.0, min(100.0, 0.6 * objective_score + 0.3 * request_match_score + affinity)),
        reasons=reasons,
    )


def rank_candidates(candidates: Iterable[Any], plan: SearchPlanV1, *, affinity_profile: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for candidate in candidates:
        match = evaluate_candidate(candidate, plan, affinity_profile=affinity_profile)
        data = _candidate_data(candidate)
        data["match"] = match.model_dump()
        # Surface score fields at the row level for API/pipeline adapters while
        # keeping the full criterion audit nested under ``match``.
        data.update({
            "status": match.status,
            "objective_score": match.objective_score,
            "request_match_score": match.request_match_score,
            "preference_affinity": match.preference_affinity,
            "personalized_score": match.personalized_score,
            "final_score": match.final_score,
        })
        # Hard rejects never enter useful results.  Unverified candidates are
        # retained and visibly labelled for the caller to investigate.
        if match.status != "rejected":
            ranked.append(data)
    ranked.sort(key=lambda row: (
        1 if row["match"]["status"] == "confirmed" else 0,
        row["match"]["personalized_score"],
        row["match"]["preference_affinity"],
        -float(row.get("price") or 0),
        str(row.get("external_id", "")),
    ), reverse=True)
    return ranked
