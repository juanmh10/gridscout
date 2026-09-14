"""Deterministic local compiler from natural language to SearchPlanV1."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .models import (
    BudgetV1,
    ConditionV1,
    CriterionOperator,
    LocationV1,
    SearchCriterionV1,
    SearchIntentV1,
    SearchPlanV1,
)
from .taxonomy import TAXONOMY_VERSION, canonical_category, fold


_BRAND_ALIASES = {
    "samsung": "Samsung",
    "lenovo": "Lenovo",
    "dell": "Dell",
    "acer": "Acer",
    "apple": "Apple",
    "asus": "ASUS",
    "amd": "AMD",
    "intel": "Intel",
    "nvidia": "NVIDIA",
    "sony": "Sony",
    "playstation": "Sony",
    "microsoft": "Microsoft",
    "xbox": "Microsoft",
    "nintendo": "Nintendo",
    "logitech": "Logitech",
    "razer": "Razer",
}
_CONDITION_ALIASES = {
    "novo": ConditionV1.NEW,
    "novos": ConditionV1.NEW,
    "nova": ConditionV1.NEW,
    "usado": ConditionV1.USED,
    "usados": ConditionV1.USED,
    "usada": ConditionV1.USED,
    "seminovo": ConditionV1.USED,
    "semi-novo": ConditionV1.USED,
    "recondicionado": ConditionV1.REFURBISHED,
    "refurbished": ConditionV1.REFURBISHED,
}


def _clean(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value).strip()


def _criterion(field: str, operator: CriterionOperator, value: Any, *, unit: str | None = None, origin: str = "user", weight: float = 1.0) -> SearchCriterionV1:
    return SearchCriterionV1(field=field, operator=operator, value=value, unit=unit, origin=origin, weight=weight)


def _criterion_values(criteria: list[SearchCriterionV1], field: str) -> list[str]:
    values: list[str] = []
    for criterion in criteria:
        if criterion.field != field:
            continue
        raw_values = criterion.value if isinstance(criterion.value, list) else [criterion.value]
        values.extend(str(value).strip() for value in raw_values if str(value).strip())
    return values


def _olx_retrieval_queries(intent: SearchIntentV1, criteria: list[SearchCriterionV1]) -> tuple[list[str], list[str]]:
    """Build short, title-indexable queries for the OLX browser search.

    The browser worker can only reliably set a category path and a text query.
    Price is filtered from result cards, while attributes such as Wi-Fi bands
    need listing-detail evidence.  Those constraints must not be copied into a
    long natural-language query, because OLX commonly returns recommendation
    cards or no search results for it.
    """

    identity: list[str] = []
    if intent.models:
        identity.extend(intent.models[:2])
    elif intent.families:
        identity.extend(intent.families[:1])
    elif intent.brands:
        identity.extend(intent.brands[:1])
    if (
        intent.category
        and intent.category.casefold() not in " ".join(identity).casefold()
        and intent.category.casefold() not in {"other", "outro", "outros"}
    ):
        identity.append(intent.category)
    if not identity:
        identity.append("anuncio")

    # IPS/OLED/TN are often present in titles and narrow a category search.
    # Wi-Fi band is deliberately not a retrieval term: it is usually absent
    # from OLX titles and is verified only after loading the listing details.
    title_hints: list[str] = []
    panel_values = _criterion_values(criteria, "panel_type")
    if panel_values:
        title_hints.append(panel_values[0])
    elif _criterion_values(criteria, "ram_gb"):
        title_hints.append(f"{_criterion_values(criteria, 'ram_gb')[0]}gb")
    elif _criterion_values(criteria, "ssd_gb"):
        title_hints.append("ssd")

    base = " ".join(identity)
    primary = " ".join([*identity, *title_hints]).strip()
    fallback = [base] if base.casefold() != primary.casefold() else []
    return [primary], fallback


def normalise_olx_retrieval_queries(plan: SearchPlanV1 | dict[str, Any]) -> SearchPlanV1:
    """Replace provider-generated prose queries with OLX-safe retrieval terms."""

    plan_obj = SearchPlanV1.model_validate(plan)
    criteria = plan_obj.must or plan_obj.intent.must
    primary, fallback = _olx_retrieval_queries(plan_obj.intent, criteria)
    updates: dict[str, Any] = {"primary_queries": primary, "fallback_queries": fallback}
    if not plan_obj.native_filters:
        updates["native_filters"] = {
            "category": plan_obj.intent.category,
            "min_price": plan_obj.intent.budget.min if plan_obj.intent.budget else None,
            "max_price": plan_obj.intent.budget.max if plan_obj.intent.budget else None,
            "sort": "recent",
        }
    if not plan_obj.marketplace_review:
        updates["marketplace_review"] = [{
            "field": criterion.field,
            "meaning": _marketplace_meaning(criterion),
            "evidence_source": _evidence_source(criterion.field),
            "missing_policy": "unverified",
        } for criterion in criteria]
    return plan_obj.model_copy(update=updates)


class DeterministicSearchCompiler:
    """Compile local requests without network or model credentials.

    Parsing is intentionally conservative: a token is added as a hard
    criterion only when the taxonomy or a typed numeric pattern identifies it.
    Unknown words remain in the query and are represented as a soft text match.
    """

    taxonomy_version = TAXONOMY_VERSION

    def compile(self, request: str | dict[str, Any] | SearchIntentV1) -> SearchPlanV1:
        if isinstance(request, SearchIntentV1):
            intent = request
            source_text = intent.raw_query
        elif isinstance(request, dict):
            intent = SearchIntentV1.model_validate(request)
            source_text = intent.raw_query
        else:
            source_text = _clean(str(request))
            intent = self._intent_from_text(source_text)
        must, should, must_not = self._criteria(intent, source_text)
        # Keep intent self-contained for session persistence while the plan
        # exposes the same criteria in executable groups.
        intent.must, intent.should, intent.must_not = must, should, must_not
        primary, fallback = self._queries(intent, source_text)
        clarifications: list[str] = []
        assumptions: list[str] = []
        if not source_text and not any((intent.category, intent.models, intent.families, intent.brands)):
            clarifications.append("Qual produto, marca ou modelo você quer encontrar?")
        if "Ryzen 5" in intent.families and not intent.models:
            assumptions.append("Ryzen 5 usa equivalências locais explicitamente ordenadas; modelos iguais ou superiores podem aparecer.")
        if "Galaxy Book" in intent.families and not intent.generations:
            assumptions.append("Galaxy Book sem geração foi tratado como família e cobre as gerações 1 a 4.")
        folded_source = fold(source_text)
        broad_markers = (
            "maximo de anuncios", "maior numero de anuncios", "alto volume",
            "busca ampla", "coleta ampla", "inventario amplo", "filtrar depois",
        )
        scope_mode = "broad" if any(marker in folded_source for marker in broad_markers) else "precise"
        has_new = bool(re.search(r"\b(?:novo|nova|novos)\b", folded_source))
        has_used = bool(re.search(r"\b(?:usado|usada|usados|seminovo|semi-novo)\b", folded_source))
        if has_new and has_used:
            clarifications.append("A condição foi informada como nova e usada; qual delas deve ser obrigatória?")
        use_dataset_match = getattr(intent, "use_dataset_match", True) if hasattr(intent, "use_dataset_match") else True
        catalog_match = "notebook-brasil-v1" if intent.category == "notebook" and use_dataset_match else None
        review_items = [
            {
                "field": criterion.field,
                "meaning": _marketplace_meaning(criterion),
                "evidence_source": _evidence_source(criterion.field),
                "missing_policy": "unverified",
            }
            for criterion in must
        ]
        return SearchPlanV1(
            intent=intent,
            must=must,
            should=should,
            must_not=must_not,
            primary_queries=primary,
            fallback_queries=fallback,
            assumptions=assumptions,
            clarifications=clarifications,
            taxonomy_version=self.taxonomy_version,
            desired_count=intent.desired_count,
            max_results=min(intent.desired_count * 3, 30),
            scope_mode=scope_mode,
            use_dataset_match=use_dataset_match,
            catalog_match=catalog_match,
            native_filters={
                "category": intent.category,
                "min_price": intent.budget.min if intent.budget else None,
                "max_price": intent.budget.max if intent.budget else None,
                "sort": "recent",
            },
            marketplace_review=review_items,
        )

    def _intent_from_text(self, text: str) -> SearchIntentV1:
        normalized = _clean(text)
        folded = fold(normalized)
        category = canonical_category(normalized)
        brands = [canonical for alias, canonical in _BRAND_ALIASES.items() if re.search(rf"\b{re.escape(alias)}\b", folded)]

        families: list[str] = []
        generations: list[str] = []
        if re.search(r"galaxy\s*book", folded):
            families.append("Galaxy Book")
            category = "notebook"
            generation_range = re.search(r"galaxy\s*book\s*(?:linha|serie|series|geracao|geração)?\s*([1-4])\s*(?:a|até|-|–)\s*([1-4])", folded)
            if generation_range:
                start, end = int(generation_range.group(1)), int(generation_range.group(2))
                generations.extend(str(item) for item in range(min(start, end), max(start, end) + 1))
            else:
                generation_match = re.search(r"galaxy\s*book\s*(?:linha|serie|series|geracao|geração)?\s*([1-4])", folded)
                if generation_match:
                    generations.append(generation_match.group(1))
        if re.search(r"ryzen\s*5", folded):
            families.append("Ryzen 5")
            # A notebook/laptop token is the product category; Ryzen is the
            # processor requirement inside that product, not a category
            # override.
            if not re.search(r"\b(?:notebook|laptop|ultrabook)\b", folded):
                category = "cpu"

        models: list[str] = []
        model_patterns = (
            (r"ryzen\s*5\s*(3600|4500|5500|5600x?|7600x?)", "Ryzen 5 {}"),
            (r"ryzen\s*7\s*(5800x3d)", "Ryzen 7 {}"),
            (r"rtx\s*(3060|3070|3080|3090|4060|4070|4080|4090)", "RTX {}"),
            (r"(?:macbook\s*(?:air|pro)?\s*)?(m[123])", "{}"),
            (r"\b(ps5|playstation\s*5)\b", "PlayStation 5"),
            (r"\b(ps4|playstation\s*4)\b", "PlayStation 4"),
            (r"\b(xbox\s*(?:series\s*[xs]|one))\b", "Xbox"),
            (r"\b(nintendo\s*switch)\b", "Nintendo Switch"),
        )
        for pattern, template in model_patterns:
            for match in re.finditer(pattern, folded, flags=re.I):
                model = template.format(match.group(1).upper() if match.group(1).startswith("m") else match.group(1).upper())
                if model.casefold() not in {item.casefold() for item in models}:
                    models.append(model)
        if "Galaxy Book" in families:
            for generation in generations:
                model = f"Galaxy Book {generation}"
                if model not in models:
                    models.append(model)
        if "Ryzen 5" in families and not models and re.search(r"(?:equivalente|semelhante|superior|ou melhor|equiv)", folded):
            # The taxonomy defines the Ryzen 5 tier itself as an ordered
            # equivalence anchor when no numeric model was supplied.
            models.append("Ryzen 5")

        budget = self._budget_from_text(normalized)
        location = self._location_from_text(normalized)
        condition: ConditionV1 | None = None
        for alias, canonical in _CONDITION_ALIASES.items():
            if re.search(rf"\b{re.escape(alias)}\b", folded):
                condition = canonical
                break
        desired_match = re.search(r"(?:top|primeiro?s?|qtd|quantidade|mostrar|retornar|retorne)\s*(\d+)", folded)
        desired_count = int(desired_match.group(1)) if desired_match else 10
        # A bare "10 anúncios" is also a count request.
        if desired_match is None:
            bare_count = re.search(r"\b(\d+)\s*(?:anuncios?|resultados?|itens?)\b", folded)
            if bare_count:
                desired_count = int(bare_count.group(1))
        return SearchIntentV1(
            category=category,
            brands=brands,
            families=families,
            models=models,
            generations=generations,
            budget=budget,
            location=location,
            condition=condition,
            desired_count=desired_count,
            raw_query=normalized,
        )

    def _budget_from_text(self, text: str) -> BudgetV1 | None:
        folded = fold(text)
        numbers = re.findall(r"(?:r\$\s*)?([\d.]+(?:,\d+)?)", folded)

        def number(value: str) -> float:
            return float(value.replace(".", "").replace(",", "."))

        if not numbers:
            return None
        if re.search(r"entre\s+.*?e\s+", folded) and len(numbers) >= 2:
            return BudgetV1(min=number(numbers[0]), max=number(numbers[1]))
        if re.search(r"(?:ate|até|no maximo|no maximo de|menos de|abaixo de|<)", folded):
            return BudgetV1(max=number(numbers[-1]))
        if re.search(r"(?:a partir de|acima de|mais de|maior que|>)", folded):
            return BudgetV1(min=number(numbers[-1]))
        # A currency-marked number is an exact ceiling by convention in
        # marketplace requests; this keeps local search bounded and safe.
        if "r$" in folded:
            return BudgetV1(max=number(numbers[-1]))
        return None

    def _location_from_text(self, text: str) -> LocationV1 | None:
        folded = fold(text)
        states = {"sp": "SP", "rj": "RJ", "mg": "MG", "pr": "PR", "sc": "SC", "rs": "RS", "ba": "BA"}
        for alias, state in states.items():
            if re.search(rf"\b{alias}\b", folded):
                return LocationV1(state=state)
        cities = {"sao paulo": "São Paulo", "campinas": "Campinas", "rio de janeiro": "Rio de Janeiro", "curitiba": "Curitiba"}
        for alias, city in cities.items():
            if alias in folded:
                return LocationV1(city=city)
        return None

    def _criteria(self, intent: SearchIntentV1, text: str) -> tuple[list[SearchCriterionV1], list[SearchCriterionV1], list[SearchCriterionV1]]:
        must: list[SearchCriterionV1] = list(intent.must)
        should: list[SearchCriterionV1] = list(intent.should)
        must_not: list[SearchCriterionV1] = list(intent.must_not)
        if intent.category:
            must.append(_criterion("category", CriterionOperator.EQ, intent.category, origin="taxonomy"))
        if intent.brands:
            op = CriterionOperator.EQ if len(intent.brands) == 1 else CriterionOperator.IN
            must.append(_criterion("brand", op, intent.brands[0] if op == CriterionOperator.EQ else intent.brands, origin="taxonomy"))
        if intent.families:
            op = CriterionOperator.EQ if len(intent.families) == 1 else CriterionOperator.IN
            must.append(_criterion("family", op, intent.families[0] if op == CriterionOperator.EQ else intent.families, origin="taxonomy"))
        if intent.models:
            op = CriterionOperator.EQ if len(intent.models) == 1 else CriterionOperator.IN
            if len(intent.models) == 1 and re.search(r"(?:equivalente|semelhante|superior|ou melhor|equiv)", fold(text)):
                op = CriterionOperator.EQUIVALENT_OR_BETTER
            must.append(_criterion("model", op, intent.models[0] if op != CriterionOperator.IN else intent.models, origin="taxonomy"))
        if intent.generations:
            op = CriterionOperator.EQ if len(intent.generations) == 1 else CriterionOperator.IN
            must.append(_criterion("generation", op, intent.generations[0] if op == CriterionOperator.EQ else intent.generations, origin="taxonomy"))
        if intent.budget:
            if intent.budget.min is not None:
                must.append(_criterion("price", CriterionOperator.GTE, intent.budget.min, unit=intent.budget.currency, origin="user"))
            if intent.budget.max is not None:
                must.append(_criterion("price", CriterionOperator.LTE, intent.budget.max, unit=intent.budget.currency, origin="user"))
        if intent.location:
            location_value = intent.location if isinstance(intent.location, str) else (intent.location.state or intent.location.city)
            if location_value:
                must.append(_criterion("location", CriterionOperator.EQ, location_value, origin="user"))
        if intent.condition:
            value = intent.condition.value if isinstance(intent.condition, ConditionV1) else str(intent.condition)
            if value.casefold() == ConditionV1.ANY.value:
                value = None
            if value is not None:
                must.append(_criterion("condition", CriterionOperator.EQ, value, origin="user"))

        folded = fold(text)
        panels = [panel for panel in ("IPS", "TN", "OLED") if re.search(rf"\b{panel.casefold()}\b", folded)]
        if panels:
            # Panel type is categorical and an explicit positive panel
            # request is hard evidence. Multiple explicit panel values are an
            # allowed categorical set rather than contradictory scalar musts.
            panel_operator = CriterionOperator.EQ if len(panels) == 1 else CriterionOperator.IN
            must.append(_criterion("panel_type", panel_operator, panels[0] if len(panels) == 1 else panels, origin="taxonomy"))
        # Wireless capability is multi-valued on a device (a notebook can
        # support 2.4 and 5 GHz simultaneously); matching treats equality
        # against a list as membership while retaining the closed v1 enum.
        wifi_band: str | None = None
        if re.search(r"(?:wi\s*[- ]?fi|wifi|wireless)[^\d]{0,8}5\s*(?:ghz|g)", folded) or re.search(r"\b5\s*ghz\b", folded):
            wifi_band = "5GHz"
        elif re.search(r"(?:wi\s*[- ]?fi|wifi|wireless)[^\d]{0,8}6\s*(?:ghz|g)", folded) or re.search(r"\b6\s*ghz\b", folded):
            wifi_band = "6GHz"
        elif re.search(r"(?:wi\s*[- ]?fi|wifi|wireless)[^\d]{0,8}2[,.]?4\s*(?:ghz|g)", folded) or re.search(r"\b2[,.]?4\s*ghz\b", folded):
            wifi_band = "2.4GHz"
        if wifi_band:
            must.append(_criterion("wifi_bands", CriterionOperator.EQ, wifi_band, origin="taxonomy"))
        for field, label in (("ram_gb", "ram"), ("ssd_gb", "ssd")):
            match = re.search(rf"(\d+(?:[,.]\d+)?)\s*(gb|tb)\s*{label}\b", folded)
            if not match:
                match = re.search(rf"\b{label}\s*(\d+(?:[,.]\d+)?)\s*(gb|tb)\b", folded)
            if match:
                value = float(match.group(1).replace(",", ".")) * (1024 if match.group(2) == "tb" else 1)
                op = CriterionOperator.GTE if re.search(rf"(?:{re.escape(match.group(0))})\s*\+", folded) else CriterionOperator.EQ
                must.append(_criterion(field, op, value, unit="GB", origin="taxonomy"))

        # Explicit exclusions are hard negative constraints.  They are never
        # inferred from absence, only from language such as "sem TN".
        for panel in ("IPS", "TN", "OLED"):
            if re.search(rf"(?:sem|sem tela)\s+{panel.casefold()}\b", folded):
                must_not.append(_criterion("panel_type", CriterionOperator.EQ, panel, origin="user"))
        for model in ("Ryzen 3", "Ryzen 7", "Ryzen 9"):
            if re.search(rf"(?:sem|não|nao)\s+{re.escape(fold(model))}", folded):
                must_not.append(_criterion("model", CriterionOperator.EQ, model, origin="user"))
        def unique(items: list[SearchCriterionV1]) -> list[SearchCriterionV1]:
            result: list[SearchCriterionV1] = []
            seen: set[str] = set()
            for item in items:
                key = repr(item.model_dump(mode="json"))
                if key not in seen:
                    seen.add(key)
                    result.append(item)
            return result
        return unique(must), unique(should), unique(must_not)

    def _queries(self, intent: SearchIntentV1, text: str) -> tuple[list[str], list[str]]:
        if not text:
            return [], []
        return _olx_retrieval_queries(intent, intent.must)


def compile_search_intent(request: str | dict[str, Any] | SearchIntentV1) -> SearchPlanV1:
    """Public local compiler function used by services and tests."""

    return DeterministicSearchCompiler().compile(request)


def _evidence_source(field: str) -> str:
    return "card" if field in {"category", "brand", "family", "model", "generation", "price", "location"} else "detail"


def _marketplace_meaning(criterion: SearchCriterionV1) -> str:
    labels = {
        "category": "tipo de produto anunciado", "brand": "marca identificada no anúncio",
        "family": "família identificada no anúncio", "model": "modelo identificado no anúncio",
        "generation": "geração identificada no anúncio", "price": "preço anunciado",
        "location": "localização publicada", "condition": "condição declarada na página do anúncio",
        "panel_type": "tipo de painel declarado", "wifi_bands": "suporte de Wi-Fi declarado",
        "ram_gb": "memória RAM declarada", "ssd_gb": "armazenamento SSD declarado",
    }
    return labels.get(criterion.field, f"evidência de {criterion.field} no anúncio")


def compile_search_local(text: str, current_intent: SearchIntentV1 | dict[str, Any] | None = None) -> Any:
    """Lazy compatibility wrapper avoiding a compiler/service import cycle."""

    from .service import compile_search

    return compile_search(text, current_intent=current_intent)
