"""Deterministic, inspectable search intelligence primitives.

The package deliberately keeps search intent/plans independent from the API and
database models.  This makes the same compiler and matcher usable by the local
worker, API adapters, benchmark executor, and tests without requiring model
credentials.
"""

from .models import (
    BudgetV1,
    ConditionV1,
    CriterionOperator,
    LocationV1,
    SearchCriterionV1,
    SearchIntentV1,
    SearchPlanV1,
)
from .compiler import DeterministicSearchCompiler, compile_search_intent, normalise_olx_retrieval_queries
from .matching import (
    CandidateMatch,
    SearchCandidate,
    evaluate_candidate,
    rank_candidates,
)
from .strategy import AdaptiveSearchExecutor, SearchExecutionResult, execute_search_plan
from .service import (
    SearchCompilationResult,
    compile_search,
    compile_search_local,
    edit_search_plan,
    prepare_pipeline_scopes,
    reply_to_search_session,
    validate_search_plan,
)
from .personalization import FeedbackSignal, PersonalizationProfile, record_feedback, suggest_personalization
from .benchmark import (
    SearchBenchmarkMetrics,
    SearchBenchmarkResult,
    compare_search_strategies,
    run_search_benchmark,
    select_search_strategy,
)

__all__ = [
    "AdaptiveSearchExecutor",
    "BudgetV1",
    "CandidateMatch",
    "ConditionV1",
    "CriterionOperator",
    "DeterministicSearchCompiler",
    "LocationV1",
    "SearchCandidate",
    "SearchExecutionResult",
    "SearchCriterionV1",
    "SearchIntentV1",
    "SearchPlanV1",
    "compile_search_intent",
    "normalise_olx_retrieval_queries",
    "compile_search",
    "compile_search_local",
    "SearchCompilationResult",
    "edit_search_plan",
    "prepare_pipeline_scopes",
    "reply_to_search_session",
    "validate_search_plan",
    "FeedbackSignal",
    "PersonalizationProfile",
    "record_feedback",
    "suggest_personalization",
    "SearchBenchmarkMetrics",
    "SearchBenchmarkResult",
    "compare_search_strategies",
    "run_search_benchmark",
    "select_search_strategy",
    "evaluate_candidate",
    "execute_search_plan",
    "rank_candidates",
]
