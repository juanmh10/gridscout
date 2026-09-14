"""Structured error taxonomy for marketplace operations."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class MarketplaceOperationError(RuntimeError):
    """Structured marketplace operation error propagated across workers and schedulers."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        kind: str = "operational",  # operational | parser | session | guard | network | validation
        stage: str = "detail",  # search | detail | parse | session | guard | network
        retry_policy: str = "none",  # skipped_unverified | retryable_after_parser_update | retryable_with_backoff | waiting_worker | waiting_session | waiting_budget | blocked | none
        http_status: Optional[int] = None,
        retry_after_seconds: Optional[int] = None,
        missing_fields: Optional[List[str]] = None,
        parser_version: str = "olx-dom-2026-08-24",
        safe_diagnostics_ref: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.kind = kind
        self.stage = stage
        self.retry_policy = retry_policy
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds
        self.missing_fields = missing_fields or []
        self.parser_version = parser_version
        self.safe_diagnostics_ref = safe_diagnostics_ref
        self.detail = detail or {}
        if not self.detail:
            self.detail = {
                "code": code,
                "message": message,
                "kind": kind,
                "stage": stage,
                "retry_policy": retry_policy,
                "http_status": http_status,
                "retry_after_seconds": retry_after_seconds,
                "missing_fields": self.missing_fields,
                "parser_version": parser_version,
                "safe_diagnostics_ref": safe_diagnostics_ref,
            }
        self.status_code = http_status or (429 if kind == "guard" else 502 if kind == "parser" else 503)


class OlxAccessError(MarketplaceOperationError):
    """Backwards-compatible structured error for OLX guard, budget, and session blocks."""

    def __init__(self, code: str, detail: Dict[str, Any], status_code: int = 429):
        message = detail.get("message", code) if isinstance(detail, dict) else str(detail)
        retry_after = detail.get("retry_after_seconds") if isinstance(detail, dict) else None
        kind = "guard" if "blocked" in code or "circuit" in code else "session" if "session" in code else "operational"
        retry_policy = (
            "blocked" if "blocked" in code
            else "waiting_budget" if "budget" in code
            else "waiting_session" if "session" in code
            else "retryable_with_backoff"
        )
        super().__init__(
            code=code,
            message=message,
            kind=kind,
            stage="guard",
            retry_policy=retry_policy,
            http_status=status_code,
            retry_after_seconds=retry_after,
            detail=detail,
        )
