"""Adaptive, bounded search execution over marketplace source adapters."""

from __future__ import annotations

import time
from typing import Any, Mapping, Optional

from pydantic import BaseModel, Field

from .matching import evaluate_candidate, rank_candidates
from .models import SearchCandidate, SearchPlanV1


class SearchExecutionResult(BaseModel):
    plan: SearchPlanV1
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    rejected: list[dict[str, Any]] = Field(default_factory=list)
    queries: list[dict[str, Any]] = Field(default_factory=list)
    deduped_count: int = 0
    navigation_count: int = 0
    max_results: int = 30
    elapsed_ms: float = 0.0
    estimated_cost_usd: float = 0.0

    @property
    def confirmed(self) -> list[dict[str, Any]]:
        return [item for item in self.candidates if item.get("match", {}).get("status") == "confirmed"]

    @property
    def unverified(self) -> list[dict[str, Any]]:
        return [item for item in self.candidates if item.get("match", {}).get("status") == "unverified"]


class AdaptiveSearchExecutor:
    """Run at most three primary and three fallback queries sequentially."""

    def __init__(self, source: Any, *, sources: Mapping[str, Any] | None = None):
        if isinstance(source, Mapping) and not sources:
            self.sources = dict(source)
            self.source = next(iter(self.sources.values()), source)
        else:
            self.source = source
            self.sources = dict(sources or {})

    def _source_for(self, name: str) -> Any:
        if name in self.sources:
            return self.sources[name]
        return self.source

    async def execute(self, plan: SearchPlanV1 | dict[str, Any], *, affinity_profile: Optional[dict[str, Any]] = None, fetch_details: bool = True) -> SearchExecutionResult:
        plan = SearchPlanV1.model_validate(plan)
        started = time.monotonic()
        # This is both the maximum number of unique candidates and the maximum
        # number of detail navigations.  It is intentionally independent of
        # provider page sizes.
        cap = min(plan.desired_count * 3, 30)
        unique: dict[tuple[str, str], SearchCandidate] = {}
        trace: list[dict[str, Any]] = []
        query_groups = (("primary", plan.primary_queries[:3]), ("fallback", plan.fallback_queries[:3]))
        navigation_count = 0
        stop = False

        def confirmed_count() -> int:
            return sum(
                evaluate_candidate(candidate, plan, affinity_profile=affinity_profile).status == "confirmed"
                for candidate in unique.values()
            )

        async def fetch_new_details(new_keys: list[tuple[str, str]]) -> None:
            """Navigate only after cross-query dedupe, one query batch at a time."""
            nonlocal navigation_count
            if not fetch_details:
                return
            for key in new_keys:
                if navigation_count >= cap:
                    return
                summary = unique[key]
                source = self._source_for(summary.source)
                # An attempted detail navigation consumes budget even when the
                # marketplace adapter errors.
                navigation_count += 1
                try:
                    detail = await source.fetch_listing(summary.external_id)
                    detail_data = detail.model_dump() if hasattr(detail, "model_dump") else dict(detail)
                    merged = summary.model_dump()
                    merged.update({
                        "title": detail_data.get("title") or merged["title"],
                        "price": detail_data.get("price", merged.get("price")),
                        "location": ", ".join(item for item in (detail_data.get("location_city"), detail_data.get("location_state")) if item) or merged.get("location", ""),
                        "condition": detail_data.get("condition") or merged.get("condition", ""),
                        "description": detail_data.get("description") or "",
                        "attributes": detail_data.get("attributes") or merged.get("attributes") or {},
                        "url": detail_data.get("source_url") or merged.get("url", ""),
                    })
                    unique[key] = SearchCandidate.model_validate(merged)
                except Exception as exc:
                    # Summary evidence remains useful, but its unavailability
                    # is visible as an unverified result through unknown musts.
                    trace.append({"group": "detail", "source": summary.source, "external_id": summary.external_id, "error": f"{type(exc).__name__}: {exc}"})

        for group, queries in query_groups:
            for query in queries:
                if stop or len(unique) >= cap or navigation_count >= cap:
                    break
                provider_items = list(self.sources.items()) if self.sources else [("fixture", self.source)]
                for source_name, source in provider_items:
                    if stop or len(unique) >= cap or navigation_count >= cap:
                        break
                    try:
                        # Keep the search contract importable in lightweight
                        # local environments where Playwright is not installed;
                        # the marketplace adapter is loaded only on execution.
                        try:
                            from packages.marketplace.source import SearchQuery
                        except Exception:
                            class SearchQuery:  # pragma: no cover - dependency-light test fallback
                                def __init__(self, **values: Any):
                                    self.__dict__.update(values)
                        summaries = await source.search(SearchQuery(
                            query=query,
                            category=plan.intent.category,
                            min_price=plan.intent.budget.min if plan.intent.budget else None,
                            max_price=plan.intent.budget.max if plan.intent.budget else None,
                            limit=min(cap, 30),
                        ))
                        error = None
                    except Exception as exc:
                        summaries = []
                        error = f"{type(exc).__name__}: {exc}"
                    added = 0
                    new_keys: list[tuple[str, str]] = []
                    for summary in summaries:
                        data = summary.model_dump() if hasattr(summary, "model_dump") else dict(summary)
                        external_id = str(data.get("external_id") or data.get("marketplace_item_id") or data.get("id") or "")
                        if not external_id:
                            continue
                        key = (source_name, external_id)
                        if key in unique:
                            continue
                        unique[key] = SearchCandidate(
                            source=source_name,
                            external_id=external_id,
                            title=str(data.get("title") or ""),
                            price=data.get("price"),
                            location=str(data.get("location") or ""),
                            condition=str(data.get("condition") or ""),
                            url=str(data.get("url") or data.get("source_url") or ""),
                            attributes=dict(data.get("attributes") or {}),
                            description=str(data.get("description") or ""),
                        )
                        added += 1
                        new_keys.append(key)
                        if len(unique) >= cap:
                            break
                    # Evaluate this query/provider batch before deciding if a
                    # fallback query is needed.  The stop decision is based on
                    # confirmed results, never merely unique summaries.
                    await fetch_new_details(new_keys)
                    confirmed_after = confirmed_count()
                    trace.append({"group": group, "source": source_name, "query": query, "received": len(summaries), "added": added, "confirmed_after": confirmed_after, "navigation_count": navigation_count, "error": error})
                    if confirmed_after >= plan.desired_count or len(unique) >= cap or navigation_count >= cap:
                        stop = True
                        break
            # Primary queries are exhausted before fallback; fallback is only
            # reached if the bounded candidate budget remains and the requested
            # number of confirmed candidates was not achieved.
            if stop:
                break

        all_matches: list[dict[str, Any]] = []
        for candidate in unique.values():
            match = evaluate_candidate(candidate, plan, affinity_profile=affinity_profile)
            if match.status == "rejected":
                row = candidate.model_dump()
                row["match"] = match.model_dump()
                row.update({
                    "status": match.status,
                    "objective_score": match.objective_score,
                    "request_match_score": match.request_match_score,
                    "preference_affinity": match.preference_affinity,
                    "personalized_score": match.personalized_score,
                    "final_score": match.final_score,
                })
                all_matches.append(row)
        ranked = rank_candidates(unique.values(), plan, affinity_profile=affinity_profile)
        return SearchExecutionResult(
            plan=plan,
            candidates=ranked[:cap],
            rejected=all_matches,
            queries=trace,
            deduped_count=len(unique),
            navigation_count=navigation_count,
            max_results=cap,
            elapsed_ms=round((time.monotonic() - started) * 1000.0, 3),
            estimated_cost_usd=0.0,
        )


async def execute_search_plan(plan: SearchPlanV1 | dict[str, Any], source: Any, **kwargs: Any) -> SearchExecutionResult:
    return await AdaptiveSearchExecutor(source).execute(plan, **kwargs)
