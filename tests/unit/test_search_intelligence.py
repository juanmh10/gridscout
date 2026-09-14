import pytest
from pydantic import ValidationError

from packages.search import (
    CriterionOperator,
    SearchCandidate,
    SearchCriterionV1,
    compile_search_local,
    evaluate_candidate,
    execute_search_plan,
    prepare_pipeline_scopes,
    record_feedback,
    suggest_personalization,
)
from packages.search.benchmark import SearchBenchmarkMetrics, SearchBenchmarkResult, compare_search_strategies, load_search_dataset, select_search_strategy


def test_local_compiler_is_versioned_and_extracts_hard_typed_constraints():
    result = compile_search_local("Samsung Galaxy Book 3 16GB RAM IPS até R$ 3000 SP")
    assert result.status == "ready"
    assert result.intent.schema_version == "search-intent-v1"
    assert result.plan.schema_version == "search-plan-v1"
    assert result.intent.desired_count == 10
    assert result.intent.category == "notebook"
    assert result.intent.families == ["Galaxy Book"]
    assert any(item.field == "panel_type" and item.operator == CriterionOperator.EQ for item in result.plan.must)
    assert any(item.field == "ram_gb" and item.unit == "GB" for item in result.plan.must)
    assert result.plan.max_results == 30
    assert len(result.plan.primary_queries) <= 3
    assert len(result.plan.fallback_queries) <= 3


def test_olx_query_uses_title_indexable_terms_and_keeps_wifi_for_detail_evidence():
    result = compile_search_local("Notebook com tela IPS, Wi-Fi 5 GHz, até R$ 2.500")

    assert result.plan.primary_queries == ["notebook IPS"]
    assert result.plan.fallback_queries == ["notebook"]
    assert all("wifi" not in query.casefold() and "2500" not in query for query in result.plan.as_scope_queries())
    review = {item["field"]: item for item in result.plan.marketplace_review}
    assert review["wifi_bands"]["evidence_source"] == "detail"
    assert review["wifi_bands"]["missing_policy"] == "unverified"
    assert result.plan.native_filters["max_price"] == 2500


def test_scope_mode_is_broad_only_when_requested_explicitly():
    assert compile_search_local("notebook até R$ 2500").plan.scope_mode == "precise"
    assert compile_search_local("busca ampla de notebook para filtrar depois").plan.scope_mode == "broad"


def test_notebook_processor_request_keeps_product_category_and_storage_constraints():
    result = compile_search_local("Notebook tela IPS Ryzen 5 semelhante ou superior, 16GB RAM e SSD 512GB")
    assert result.intent.category == "notebook"
    by_field = {item.field: item for item in result.plan.must}
    assert by_field["category"].value == "notebook"
    assert by_field["panel_type"].operator == CriterionOperator.EQ
    assert by_field["panel_type"].value == "IPS"
    assert by_field["ram_gb"].value == 16
    assert by_field["ssd_gb"].value == 512
    assert by_field["ram_gb"].unit == by_field["ssd_gb"].unit == "GB"
    assert by_field["model"].operator == CriterionOperator.EQUIVALENT_OR_BETTER
    assert by_field["model"].value == "Ryzen 5"
    assert "OLED" not in str(by_field["panel_type"].value)
    oled = SearchCandidate(external_id="oled", title="Notebook tela OLED Ryzen 5 5600 16GB RAM SSD 512GB")
    assert evaluate_candidate(oled, result.plan).status == "rejected"


def test_session_reply_inherits_only_missing_intent_fields():
    result = compile_search_local("até R$ 3000", current_intent={"category": "notebook", "brands": ["Samsung"]})
    assert result.intent.category == "notebook"
    assert result.intent.brands == ["Samsung"]
    assert result.intent.budget.max == 3000


def test_versioned_dataset_covers_taxonomy_alias_units_and_contradictions():
    version, cases = load_search_dataset()
    assert version == "search-v1.1.0"
    assert len(cases) >= 9
    range_plan = compile_search_local("Samsung Galaxy Book 1 a 4 notebook").plan
    assert range_plan.intent.generations == ["1", "2", "3", "4"]
    storage_plan = compile_search_local("notebook 16GB RAM 1TB SSD").plan
    assert {item.field for item in storage_plan.must} >= {"ram_gb", "ssd_gb"}
    assert compile_search_local("Samsung GalaxyBook").intent.families == ["Galaxy Book"]
    assert compile_search_local("notebook novo usado").status == "needs_clarification"


def test_criterion_operators_are_closed_and_ranges_are_validated():
    with pytest.raises(ValidationError):
        SearchCriterionV1(field="price", operator="contains", value=100)
    with pytest.raises(ValidationError):
        SearchCriterionV1(field="price", operator="range", value=[100])
    assert SearchCriterionV1(field="price", operator="range", value=[100, 200]).operator == CriterionOperator.RANGE


def test_equivalent_or_better_is_explicitly_ordered_and_unknown_must_is_unverified():
    result = compile_search_local("Ryzen 5 5600 equivalente ou melhor")
    criterion = next(item for item in result.plan.must if item.field == "model")
    assert criterion.operator == CriterionOperator.EQUIVALENT_OR_BETTER
    better = SearchCandidate(external_id="better", title="AMD Ryzen 5 7600", attributes={"category": "cpu", "family": "Ryzen 5"})
    assert evaluate_candidate(better, result.plan).status == "confirmed"
    unknown = SearchCandidate(external_id="unknown", title="processador", attributes={})
    assert evaluate_candidate(unknown, result.plan).status == "unverified"


def test_affinity_is_bounded_and_does_not_change_final_score():
    result = compile_search_local("notebook Samsung Galaxy Book").plan
    candidate = SearchCandidate(external_id="x", title="Samsung Galaxy Book notebook", attributes={"category": "notebook", "brand": "Samsung", "family": "Galaxy Book"})
    match = evaluate_candidate(candidate, result, affinity_profile={"preferred_brands": ["Samsung"], "category_expertise": {"notebook": 1}})
    assert -10 <= match.affinity <= 10
    assert match.final_score == match.objective_score
    assert match.personalized_score == max(0, min(100, 0.6 * match.objective_score + 0.3 * match.request_match_score + match.affinity))


class _Source:
    def __init__(self):
        self.detail_calls = []

    async def search(self, query):
        return [
            SearchCandidate(external_id="1", title="Samsung Galaxy Book 3 notebook", attributes={"category": "notebook", "brand": "Samsung", "family": "Galaxy Book", "generation": "3"}),
            SearchCandidate(external_id="1", title="duplicate", attributes={}),
            SearchCandidate(external_id="2", title="Samsung Galaxy Book 4 notebook", attributes={"category": "notebook", "brand": "Samsung", "family": "Galaxy Book", "generation": "4"}),
        ]

    async def fetch_listing(self, external_id):
        self.detail_calls.append(external_id)
        return SearchCandidate(external_id=external_id, title="Samsung Galaxy Book 3 notebook", attributes={"category": "notebook", "brand": "Samsung", "family": "Galaxy Book", "generation": "3"})


@pytest.mark.asyncio
async def test_adaptive_dedupes_before_detail_navigation():
    source = _Source()
    plan = compile_search_local("Samsung Galaxy Book 3").plan
    result = await execute_search_plan(plan, source)
    assert result.deduped_count == 2
    assert source.detail_calls == ["1", "2"]


def test_personalization_suggestion_requires_three_auditable_signals():
    profile = None
    for index in range(2):
        profile = record_feedback(profile, {"candidate_id": str(index), "signal": "saved", "brand": "Samsung", "category": "notebook", "reason": "bom estado"})
    assert suggest_personalization(profile) is None
    profile = record_feedback(profile, {"candidate_id": "3", "signal": "clicked", "brand": "Samsung", "category": "notebook", "reason": "preço"})
    suggestion = suggest_personalization(profile)
    assert suggestion and suggestion["signal_count"] == 3


def test_benchmark_compares_and_selects_lexicographically():
    results = compare_search_strategies()
    assert {item.strategy for item in results} == {"literal", "static", "adaptive"}
    assert select_search_strategy(results).strategy in {"static", "adaptive"}


def test_adaptive_promotion_requires_relevance_or_coverage_without_precision_loss():
    literal = SearchBenchmarkResult(strategy="literal", dataset_version="x", metrics=SearchBenchmarkMetrics(p_at_10=0.8, r_at_30=0.5, ndcg_at_10=0.5, navigation_count=2))
    adaptive_worse_precision = SearchBenchmarkResult(strategy="adaptive", dataset_version="x", metrics=SearchBenchmarkMetrics(p_at_10=0.7, r_at_30=1.0, ndcg_at_10=1.0, navigation_count=4))
    static = SearchBenchmarkResult(strategy="static", dataset_version="x", metrics=SearchBenchmarkMetrics(p_at_10=0.8, r_at_30=0.6, ndcg_at_10=0.6, navigation_count=3))
    assert select_search_strategy([literal, adaptive_worse_precision, static]).strategy == "static"
    adaptive = adaptive_worse_precision.model_copy(update={"metrics": adaptive_worse_precision.metrics.model_copy(update={"p_at_10": 0.8})})
    assert select_search_strategy([literal, adaptive, static]).strategy == "adaptive"


class _ConfirmedBatchSource:
    def __init__(self):
        self.search_calls = []
        self.detail_calls = []

    async def search(self, query):
        self.search_calls.append(query.query)
        return [SearchCandidate(external_id="confirmed", title="Samsung Galaxy Book notebook", attributes={"category": "notebook", "brand": "Samsung", "family": "Galaxy Book"})]

    async def fetch_listing(self, external_id):
        self.detail_calls.append(external_id)
        return SearchCandidate(external_id=external_id, title="Samsung Galaxy Book notebook", attributes={"category": "notebook", "brand": "Samsung", "family": "Galaxy Book"})


@pytest.mark.asyncio
async def test_adaptive_stops_before_fallback_after_confirmed_batch():
    source = _ConfirmedBatchSource()
    compiled = compile_search_local("Samsung Galaxy Book").plan
    intent = compiled.intent.model_copy(update={"desired_count": 1})
    plan = compiled.model_copy(update={"intent": intent, "desired_count": 1, "max_results": 3})
    result = await execute_search_plan(plan, source)
    assert len(source.search_calls) == 1
    assert source.detail_calls == ["confirmed"]
    assert len(result.confirmed) == 1


def test_pipeline_scope_snapshots_share_the_plan_candidate_cap():
    compiled = compile_search_local("notebook IPS Wi-Fi 5 GHz até 2500").plan
    plan = compiled.model_copy(update={
        "primary_queries": ["notebook ips", "notebook wifi 5 ghz", "notebook tela ips"],
        "fallback_queries": ["notebook 5 ghz", "notebook ips usado", "notebook ips 2500"],
        "max_results": 30,
    })

    scopes = prepare_pipeline_scopes(plan, marketplace="olx")

    assert len(scopes) == 6
    assert [scope["limit"] for scope in scopes] == [5, 5, 5, 5, 5, 5]
    assert sum(scope["limit"] for scope in scopes) == 30


def test_dataset_matching_activation_and_deactivation():
    """Compiling a notebook search should activate catalog_match by default and allow deactivation."""
    # Active by default for notebook with IPS criteria
    result_active = compile_search_local("Notebook tela IPS até 3000")
    assert result_active.plan.use_dataset_match is True
    assert result_active.plan.catalog_match == "notebook-brasil-v1"
    assert "notebook IPS" in result_active.plan.primary_queries
    scopes_active = prepare_pipeline_scopes(result_active.plan, marketplace="olx")
    assert all(scope.get("catalog_match") == "notebook-brasil-v1" for scope in scopes_active)

    # Deactivated explicit
    plan_disabled = result_active.plan.model_copy(update={"use_dataset_match": False, "catalog_match": None})
    scopes_disabled = prepare_pipeline_scopes(plan_disabled, marketplace="olx")
    assert all(scope.get("catalog_match") is None for scope in scopes_disabled)

