"""Provider-neutral services used by API adapters and workers."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from .compiler import compile_search_intent
from .models import SearchIntentV1, SearchPlanV1


class SearchCompilationResult(BaseModel):
    """Stable response for chat/session adapters."""

    status: str = "ready"
    intent: SearchIntentV1
    plan: SearchPlanV1
    clarification: Optional[str] = None
    assumptions: list[str] = Field(default_factory=list)

    @property
    def needs_clarification(self) -> bool:
        return self.status == "needs_clarification"


def compile_search(text: str, current_intent: SearchIntentV1 | dict[str, Any] | None = None) -> SearchCompilationResult:
    """Compile a chat message and return intent, plan and optional clarification.

    The function is synchronous and deterministic so a FastAPI endpoint can
    call it without creating a model task.  ``current_intent`` is merged only
    for missing fields, which lets a session reply such as ``"até R$ 3000"``
    refine an earlier product request.
    """

    text = str(text or "").strip()
    if current_intent is None:
        request: str | dict[str, Any] = text
    else:
        base = current_intent.model_dump() if isinstance(current_intent, SearchIntentV1) else dict(current_intent)
        # Parsing the reply independently means explicit new entities win;
        # absent entities inherit the previous turn.
        parsed = compile_search_intent(text).intent
        merged = parsed.model_dump()
        for field in ("category", "brands", "families", "models", "generations", "budget", "location", "condition"):
            value = merged.get(field)
            if value in (None, [], ""):
                inherited = base.get(field)
                merged[field] = inherited if inherited is not None else ([] if field in {"brands", "families", "models", "generations"} else None)
        for field in ("must", "should", "must_not"):
            if not merged.get(field):
                merged[field] = base.get(field, [])
        merged["desired_count"] = parsed.desired_count if parsed.desired_count != 10 else base.get("desired_count", 10)
        merged["raw_query"] = " ".join(item for item in (base.get("raw_query", ""), text) if item).strip()
        request = merged
    plan = compile_search_intent(request)
    if plan.clarifications:
        return SearchCompilationResult(
            status="needs_clarification",
            intent=plan.intent,
            plan=plan,
            clarification=plan.clarifications[0],
            assumptions=plan.assumptions,
        )
    return SearchCompilationResult(status="ready", intent=plan.intent, plan=plan, assumptions=plan.assumptions)


def reply_to_search_session(text: str, current_intent: SearchIntentV1 | dict[str, Any] | None = None) -> SearchCompilationResult:
    """Alias with a conversational name for integrations."""

    return compile_search(text, current_intent=current_intent)


def compile_search_local(text: str, current_intent: SearchIntentV1 | dict[str, Any] | None = None) -> SearchCompilationResult:
    """Compatibility name for local-only session integrations."""

    return compile_search(text, current_intent=current_intent)


def validate_search_plan(plan: SearchPlanV1 | dict[str, Any]) -> SearchPlanV1:
    """Validate a complete plan; partial provider output is not accepted."""

    return SearchPlanV1.model_validate(plan)


def edit_search_plan(plan: SearchPlanV1 | dict[str, Any], changes: dict[str, Any]) -> SearchPlanV1:
    """Apply an explicit user edit and revalidate the complete plan."""

    current = validate_search_plan(plan).model_dump(mode="json")
    _deep_update(current, changes)
    return validate_search_plan(current)


def prepare_pipeline_scopes(plan: SearchPlanV1 | dict[str, Any], *, marketplace: str = "fixture") -> list[dict[str, Any]]:
    """Translate a plan into immutable pipeline scope snapshots.

    Each query remains auditable and carries the full plan.  Hard criteria are
    still enforced by the search executor after marketplace retrieval.
    """

    plan_obj = validate_search_plan(plan)
    queries = plan_obj.as_scope_queries()
    if not queries:
        queries = [plan_obj.intent.raw_query]
    # ``max_results`` is the candidate budget for the whole plan, not for
    # every wording of that plan.  Splitting it across the ordered queries
    # keeps a precise scope executable within the OLX navigation guard while
    # still recording every query used to retrieve candidates.
    candidate_cap = max(1, min(plan_obj.max_results, 30))
    queries = queries[:min(6, candidate_cap)]
    base_limit, remainder = divmod(candidate_cap, len(queries))
    scopes = [
        {
            "name": f"Busca {index + 1}",
            "marketplace": marketplace,
            "query": query,
            "category": plan_obj.intent.category,
            "min_price": plan_obj.intent.budget.min if plan_obj.intent.budget else None,
            "max_price": plan_obj.intent.budget.max if plan_obj.intent.budget else None,
            "require_price": plan_obj.require_price,
            "olx_pay_only": plan_obj.olx_pay_only,
            "delivery_only": plan_obj.delivery_only,
            "sort": "recent",
            "limit": base_limit + (1 if index < remainder else 0),
            "plan": plan_obj.model_dump(mode="json"),
            "scope_mode": plan_obj.scope_mode,
        }
        for index, query in enumerate(queries)
    ]
    # A price-only notebook request is intentionally broad.  Its marketplace
    # results are compared with the canonical Brazilian notebook catalogue in
    # the pipeline after detail capture.  This is one shared candidate budget,
    # not one remote search budget per catalogue model.
    try:
        from packages.catalog import NOTEBOOK_CATALOG_MODE, is_free_model_notebook_plan

        if is_free_model_notebook_plan(plan_obj):
            for scope in scopes:
                scope["catalog_match"] = NOTEBOOK_CATALOG_MODE
    except ImportError:  # pragma: no cover - keeps the contracts standalone
        pass
    return scopes


async def execute_compiled_search(plan: SearchPlanV1 | dict[str, Any], source: Any, **kwargs: Any) -> Any:
    from .strategy import execute_search_plan

    return await execute_search_plan(plan, source, **kwargs)


def _deep_update(target: dict[str, Any], changes: dict[str, Any]) -> None:
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
