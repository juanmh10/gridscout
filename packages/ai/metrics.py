"""Persisted, content-free model invocation telemetry and published price rules."""

from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
import time
import uuid
from typing import Any, Iterator, Optional

from packages.core.database import SessionLocal
from packages.core.models import ModelMetricCall

_metric_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "model_metric_context", default={}
)


@contextlib.contextmanager
def model_metric_context(
    *,
    pipeline_run_id: Optional[str] = None,
    attempt_number: Optional[int] = None,
    task_kind: Optional[str] = None,
    task_id: Optional[str] = None,
    origin: str = "pipeline",
    operation: Optional[str] = None,
    normalization_quality: Optional[str] = None,
    response_status: Optional[str] = None,
    provider_status: Optional[str] = None,
) -> Iterator[None]:
    token = _metric_context.set({
        "pipeline_run_id": pipeline_run_id,
        "attempt_number": attempt_number,
        "task_kind": task_kind,
        "task_id": task_id,
        "origin": origin,
        "operation": operation,
        "normalization_quality": normalization_quality,
        "response_status": response_status,
        "provider_status": provider_status,
    })
    try:
        yield
    finally:
        _metric_context.reset(token)


def metric_started() -> tuple[dt.datetime, float]:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None), time.monotonic()


def _int_attr(value: Any, *names: str) -> int:
    for name in names:
        candidate = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
        if candidate is not None:
            try:
                return int(candidate)
            except (TypeError, ValueError):
                return 0
    return 0


def response_usage(response: Any) -> dict[str, int | str | None]:
    usage = getattr(response, "usage_metadata", None) or getattr(response, "usage", None) or {}
    prompt = _int_attr(usage, "prompt_token_count", "prompt_tokens", "input_tokens")
    candidates = _int_attr(usage, "candidates_token_count", "candidates_tokens", "output_tokens")
    thoughts = _int_attr(usage, "thoughts_token_count", "thoughts_tokens")
    cached = _int_attr(usage, "cached_content_token_count", "cached_tokens", "cache_read_input_tokens")
    tool = _int_attr(usage, "tool_use_prompt_token_count", "tool_tokens")
    total = _int_attr(usage, "total_token_count", "total_tokens") or (prompt + candidates + thoughts + cached + tool)
    finish_reason = None
    candidates_value = getattr(response, "candidates", None)
    if candidates_value:
        finish_reason = str(getattr(candidates_value[0], "finish_reason", "") or "") or None
    return {
        "prompt_tokens": prompt,
        "candidates_tokens": candidates,
        "thoughts_tokens": thoughts,
        "cached_tokens": cached,
        "tool_tokens": tool,
        "total_tokens": total,
        "finish_reason": finish_reason,
    }


def estimate_cost(model_id: str, started_at: dt.datetime, usage: dict[str, Any]) -> tuple[Optional[float], Optional[str]]:
    """Paid standard USD prices, versioned at the published Gemini 3.7 change."""
    normalized = (model_id or "").lower().split("/")[-1]
    if normalized == "gemini-3.5-flash-lite":
        rates, version = (0.30, 2.50, 0.03), "gemini-3.5-flash-lite-paid-standard-v1"
    elif normalized == "gemini-3.7-flash":
        if started_at.date() <= dt.date(2026, 12, 31):
            rates, version = (0.75, 3.75, 0.075), "gemini-3.7-flash-paid-standard-2026"
        else:
            rates, version = (1.50, 7.50, 0.15), "gemini-3.7-flash-paid-standard-2027"
    else:
        return None, None
    input_rate, output_rate, cache_rate = rates
    cost = (
        int(usage.get("prompt_tokens", 0)) * input_rate
        + (int(usage.get("candidates_tokens", 0)) + int(usage.get("thoughts_tokens", 0))) * output_rate
        + int(usage.get("cached_tokens", 0)) * cache_rate
    ) / 1_000_000
    return round(cost, 12), version


def record_model_metric(
    *, operation: str, provider: str, auth_mode: str, model_id: str,
    started_at: dt.datetime, started_monotonic: float, response: Any = None,
    status: str = "success", error_code: Optional[str] = None,
    normalization_quality: Optional[str] = None,
    response_status: Optional[str] = None,
    provider_status: Optional[str] = None,
) -> None:
    """Best effort only: telemetry must never change inference behaviour."""
    context = _metric_context.get()
    operation = context.get("operation") or operation
    quality = normalization_quality or context.get("normalization_quality")
    resp_status = response_status or context.get("response_status")
    prov_status = provider_status or context.get("provider_status")
    usage = response_usage(response) if response is not None else {
        "prompt_tokens": 0, "candidates_tokens": 0, "thoughts_tokens": 0,
        "cached_tokens": 0, "tool_tokens": 0, "total_tokens": 0, "finish_reason": None,
    }
    cost, pricing_version = estimate_cost(model_id, started_at, usage)
    db = SessionLocal()
    try:
        db.add(ModelMetricCall(
            id=f"mmc-{uuid.uuid4().hex}",
            pipeline_run_id=context.get("pipeline_run_id"),
            attempt_number=context.get("attempt_number"),
            task_kind=context.get("task_kind"),
            task_id=context.get("task_id"),
            origin=context.get("origin") or "pipeline",
            operation=operation,
            provider=provider,
            auth_mode=auth_mode,
            model_id=model_id,
            status=status,
            started_at=started_at,
            duration_ms=round((time.monotonic() - started_monotonic) * 1000, 3),
            prompt_tokens=int(usage["prompt_tokens"]),
            candidates_tokens=int(usage["candidates_tokens"]),
            thoughts_tokens=int(usage["thoughts_tokens"]),
            cached_tokens=int(usage["cached_tokens"]),
            tool_tokens=int(usage["tool_tokens"]),
            total_tokens=int(usage["total_tokens"]),
            finish_reason=usage["finish_reason"],
            estimated_cost_usd=cost,
            pricing_version=pricing_version,
            normalization_quality=quality,
            response_status=resp_status,
            provider_status=prov_status,
            error_code=error_code,
        ))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

