"""Fixture benchmark for literal/static/adaptive search strategies."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from pydantic import BaseModel, Field

from .compiler import compile_search_intent
from .matching import evaluate_candidate, rank_candidates
from .models import SearchCandidate


DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "fixtures" / "evals" / "search_ground_truth_v1.json"


class SearchBenchmarkCase(BaseModel):
    id: str
    query: str
    relevant_ids: list[str] = Field(default_factory=list)
    expected_intent: dict[str, Any] = Field(default_factory=dict)
    candidates: list[dict[str, Any]] = Field(default_factory=list)


class SearchBenchmarkMetrics(BaseModel):
    intent_f1: float = 0.0
    hard_violations: float = 0.0
    p_at_10: float = 0.0
    r_at_30: float = 0.0
    ndcg_at_10: float = 0.0
    relevant_navigation: float = 0.0
    navigation_count: float = 0.0
    dedupe_rate: float = 1.0
    latency_ms: float = 0.0
    cost_usd: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return self.model_dump()


class SearchBenchmarkResult(BaseModel):
    strategy: str
    dataset_version: str
    metrics: SearchBenchmarkMetrics
    cases: list[dict[str, Any]] = Field(default_factory=list)


def load_search_dataset(path: str | Path | None = None) -> tuple[str, list[SearchBenchmarkCase]]:
    dataset_path = Path(path) if path else DEFAULT_DATASET
    if not dataset_path.exists():
        return "search-v1", []
    data = json.loads(dataset_path.read_text(encoding="utf-8"))
    return str(data.get("dataset_version", "search-v1")), [SearchBenchmarkCase.model_validate(item) for item in data.get("cases", [])]


def _intent_f1(plan: Any, expected: Mapping[str, Any]) -> float:
    if not expected:
        return 1.0
    actual: set[tuple[str, str]] = set()
    for field in ("category", "brands", "families", "models", "generations"):
        value = getattr(plan.intent, field, None)
        if isinstance(value, list):
            actual.update((field, str(item).casefold()) for item in value)
        elif value:
            actual.add((field, str(value).casefold()))
    wanted: set[tuple[str, str]] = set()
    for field, value in expected.items():
        if isinstance(value, list):
            wanted.update((field, str(item).casefold()) for item in value)
        elif value:
            wanted.add((field, str(value).casefold()))
    if not wanted:
        return 1.0
    precision = len(actual & wanted) / len(actual) if actual else 0.0
    recall = len(actual & wanted) / len(wanted)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _ndcg(ids: list[str], relevant: set[str], limit: int = 10) -> float:
    ranked = list(dict.fromkeys(ids))[:limit]
    dcg = sum((1.0 / math.log2(index + 2)) for index, item in enumerate(ranked) if item in relevant)
    ideal = sum((1.0 / math.log2(index + 2)) for index in range(min(len(relevant), limit)))
    return dcg / ideal if ideal else 1.0


def _dedupe_rows(rows: list[SearchCandidate]) -> list[SearchCandidate]:
    seen: set[tuple[str, str]] = set()
    result: list[SearchCandidate] = []
    for row in rows:
        key = (row.source, row.external_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def run_search_benchmark(*, dataset_path: str | Path | None = None, strategy: str = "adaptive") -> SearchBenchmarkResult:
    dataset_version, cases = load_search_dataset(dataset_path)
    started = time.monotonic()
    all_case_results: list[dict[str, Any]] = []
    intent_scores: list[float] = []
    violations: list[float] = []
    precisions: list[float] = []
    recalls: list[float] = []
    ndcgs: list[float] = []
    navs: list[float] = []
    navigation_counts: list[float] = []
    dedupes: list[float] = []
    for case in cases:
        plan = compile_search_intent(case.query)
        # The benchmark executor uses deterministic fixture rows directly so
        # worker runs do not require browser credentials or network access.
        candidate_rows = [SearchCandidate.model_validate(row) for row in case.candidates]
        if strategy == "literal":
            candidates = [row for row in candidate_rows if plan.intent.raw_query.casefold() in row.title.casefold()]
        elif strategy == "static":
            candidates = candidate_rows[: min(10, len(candidate_rows))]
        else:
            candidates = candidate_rows[: min(plan.max_results, len(candidate_rows))]
        candidates = _dedupe_rows(candidates)
        ranked = rank_candidates(candidates, plan)
        ids = [str(row.get("external_id")) for row in ranked]
        metric_ids = list(dict.fromkeys(ids))
        relevant = set(case.relevant_ids)
        p10 = sum(item in relevant for item in metric_ids[:10]) / 10.0
        r30 = sum(item in relevant for item in metric_ids[:30]) / len(relevant) if relevant else 1.0
        # A hard violation is counted only when a candidate was presented as
        # confirmed.  Rejected rows are expected filtering behavior and must
        # not make a strategy fail the hard-violation gate.
        hard_results = [evaluate_candidate(row, plan) for row in candidates]
        confirmed = [item for item in hard_results if item.status == "confirmed"]
        hard = sum(
            item.status == "confirmed" and any(check.get("matched") is False for check in item.must)
            for item in hard_results
        ) / len(confirmed) if confirmed else 0.0
        intent_scores.append(_intent_f1(plan, case.expected_intent))
        violations.append(hard)
        precisions.append(p10)
        recalls.append(min(1.0, r30))
        ndcgs.append(_ndcg(ids, relevant))
        navs.append(sum(item in relevant for item in metric_ids) / len(metric_ids) if metric_ids else 0.0)
        navigation_counts.append(float(len(candidates)))
        raw_count = len(candidate_rows)
        dedupes.append(len({(row.source, row.external_id) for row in candidate_rows}) / raw_count if raw_count else 1.0)
        all_case_results.append({
            "id": case.id,
            "returned_ids": ids,
            "relevant_ids": case.relevant_ids,
            "intent_f1": intent_scores[-1],
            "navigation_count": len(candidates),
            "hard_violations_confirmed": hard,
        })
    count = max(1, len(cases))
    metrics = SearchBenchmarkMetrics(
        intent_f1=sum(intent_scores) / count,
        hard_violations=sum(violations) / count,
        p_at_10=sum(precisions) / count,
        r_at_30=sum(recalls) / count,
        ndcg_at_10=sum(ndcgs) / count,
        relevant_navigation=sum(navs) / count,
        navigation_count=sum(navigation_counts) / count,
        dedupe_rate=sum(dedupes) / count,
        latency_ms=round((time.monotonic() - started) * 1000.0, 3),
        cost_usd=0.0,
    )
    return SearchBenchmarkResult(strategy=strategy, dataset_version=dataset_version, metrics=metrics, cases=all_case_results)


def compare_search_strategies(*, dataset_path: str | Path | None = None, strategies: Iterable[str] = ("literal", "static", "adaptive")) -> list[SearchBenchmarkResult]:
    return [run_search_benchmark(dataset_path=dataset_path, strategy=strategy) for strategy in strategies]


def select_search_strategy(results: Iterable[SearchBenchmarkResult]) -> SearchBenchmarkResult | None:
    """Select with the contract's lexicographic quality and safety gates.

    Adaptive is promoted over literal only when it improves relevance or
    coverage without reducing precision.  This avoids selecting an adaptive
    strategy merely because it navigated more pages.
    """

    values = list(results)
    if not values:
        return None
    eligible = [result for result in values if result.metrics.hard_violations == 0.0]
    if not eligible:
        return None
    literal = next((result for result in eligible if result.strategy == "literal"), None)
    adaptive = next((result for result in eligible if result.strategy == "adaptive"), None)
    if adaptive is not None and literal is not None:
        improves_relevance = adaptive.metrics.ndcg_at_10 > literal.metrics.ndcg_at_10
        improves_coverage = adaptive.metrics.r_at_30 > literal.metrics.r_at_30
        preserves_precision = adaptive.metrics.p_at_10 >= literal.metrics.p_at_10
        if not (preserves_precision and (improves_relevance or improves_coverage)):
            eligible = [result for result in eligible if result.strategy != "adaptive"]

    return max(eligible, key=lambda result: (
        result.metrics.ndcg_at_10,
        result.metrics.r_at_30,
        -result.metrics.navigation_count,
        -result.metrics.cost_usd,
    ))


# Short aliases for callers that treat this as the search benchmark service.
run_benchmark = run_search_benchmark
compare_strategies = compare_search_strategies
select_strategy = select_search_strategy
