"""Shared authentication selection for the Google Gen AI SDK."""

import os
from typing import Optional


_API_KEY_MODES = {"api_key", "gemini_api", "developer_api", "express"}
_VERTEX_MODES = {"vertex", "vertex_ai", "adc"}


def resolve_genai_auth_mode(
    *,
    project_id: Optional[str],
    api_key: Optional[str],
    auth_mode: Optional[str] = None,
) -> str:
    """Resolve the SDK auth path without silently mixing credentials.

    A project and an API key can legitimately coexist in the environment for
    different integrations (for example Vertex Search plus Gemini API). The
    model client therefore requires an explicit mode when both are present.
    """
    configured_mode = (auth_mode or os.getenv("GENAI_AUTH_MODE", "")).strip().lower()
    if configured_mode == "auto":
        configured_mode = ""
    has_project = bool(project_id)
    has_api_key = bool(api_key)

    if configured_mode in _API_KEY_MODES:
        if not has_api_key:
            raise ValueError("GENAI_AUTH_MODE=gemini_api requires GEMINI_API_KEY.")
        return "gemini_api"

    if configured_mode in _VERTEX_MODES:
        if not has_project:
            raise ValueError("GENAI_AUTH_MODE=vertex requires VERTEX_PROJECT_ID.")
        return "vertex"

    if configured_mode:
        raise ValueError(
            "GENAI_AUTH_MODE must be gemini_api or vertex."
        )

    if has_api_key and not has_project:
        return "gemini_api"
    if has_project and not has_api_key:
        return "vertex"
    if has_project and has_api_key:
        raise ValueError(
            "Both VERTEX_PROJECT_ID and GEMINI_API_KEY are configured; "
            "set GENAI_AUTH_MODE=gemini_api or GENAI_AUTH_MODE=vertex."
        )

    raise ValueError(
        "Configure GEMINI_API_KEY for Gemini API or VERTEX_PROJECT_ID for Vertex AI."
    )


def configured_genai_auth_mode() -> str:
    """Return the configured mode for diagnostics without reading secrets."""
    return os.getenv("GENAI_AUTH_MODE", "auto").strip().lower() or "auto"
