import json

import pytest

from apps.browser_worker import main as browser_worker


def test_browser_probe_failure_preserves_last_confirmed_session(monkeypatch, tmp_path):
    metadata_file = tmp_path / "session_metadata.json"
    metadata_file.write_text(
        json.dumps(
            {
                "email": "buyer@example.com",
                "authenticated_at": "2026-08-24T15:00:00+00:00",
                "last_verified_at": "2026-08-24T15:00:00+00:00",
                "last_session_state": "connected",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(browser_worker, "SESSION_METADATA_FILE", metadata_file)

    browser_worker._update_metadata_from_probe(
        {
            "session_state": "unavailable",
            "authenticated": False,
            "checked_at": "2026-08-24T16:00:00+00:00",
            "error_code": "olx_browser_unavailable",
            "error_message": "O Chrome visível está indisponível.",
        }
    )

    saved = json.loads(metadata_file.read_text(encoding="utf-8"))
    assert saved["last_session_state"] == "connected"
    assert saved["last_probe_state"] == "unavailable"
    assert saved["last_error_code"] == "olx_browser_unavailable"


def test_successful_probe_recreates_missing_metadata(monkeypatch, tmp_path):
    metadata_file = tmp_path / "session_metadata.json"
    monkeypatch.setattr(browser_worker, "SESSION_METADATA_FILE", metadata_file)

    browser_worker._update_metadata_from_probe(
        {
            "session_state": "connected",
            "authenticated": True,
            "checked_at": "2026-08-24T16:00:00+00:00",
            "error_code": None,
            "error_message": "",
        }
    )

    saved = json.loads(metadata_file.read_text(encoding="utf-8"))
    assert saved["marketplace"] == "olx"
    assert saved["last_session_state"] == "connected"
    assert saved["last_verified_at"] == "2026-08-24T16:00:00+00:00"


@pytest.mark.asyncio
async def test_session_status_reports_browser_unavailable_without_requesting_login(monkeypatch, tmp_path):
    session_file = tmp_path / "storage_state.json"
    metadata_file = tmp_path / "session_metadata.json"
    session_file.write_text(json.dumps({"cookies": [{"name": "session"}]}), encoding="utf-8")
    metadata_file.write_text(
        json.dumps(
            {
                "email": "buyer@example.com",
                "authenticated_at": "2026-08-24T15:00:00+00:00",
                "last_verified_at": "2026-08-24T15:00:00+00:00",
                "last_session_state": "connected",
                "last_probe_state": "connected",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(browser_worker, "SESSION_FILE", session_file)
    monkeypatch.setattr(browser_worker, "SESSION_METADATA_FILE", metadata_file)
    monkeypatch.setattr(browser_worker, "CDP_ENDPOINT", "http://127.0.0.1:9222")
    monkeypatch.setattr(browser_worker, "_login_stage", "idle")
    browser_worker._invalidate_session_probe()

    async def cdp_not_ready():
        return False

    monkeypatch.setattr(browser_worker, "_cdp_ready", cdp_not_ready)

    payload = await browser_worker._session_status_payload()

    assert payload["authenticated"] is False
    assert payload["session_state"] == "unavailable"
    assert payload["worker_available"] is False
    assert payload["browser_ready"] is False
    assert payload["error_code"] == "olx_browser_unavailable"


@pytest.mark.asyncio
async def test_session_status_does_not_treat_cookies_as_authenticated(monkeypatch, tmp_path):
    session_file = tmp_path / "storage_state.json"
    metadata_file = tmp_path / "session_metadata.json"
    session_file.write_text(json.dumps({"cookies": [{"name": "session"}]}), encoding="utf-8")
    metadata_file.write_text(
        json.dumps(
            {
                "email": "buyer@example.com",
                "authenticated_at": "2026-08-24T15:00:00+00:00",
                "last_verified_at": "2026-08-24T15:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(browser_worker, "SESSION_FILE", session_file)
    monkeypatch.setattr(browser_worker, "SESSION_METADATA_FILE", metadata_file)
    monkeypatch.setattr(browser_worker, "CDP_ENDPOINT", "")
    monkeypatch.setattr(browser_worker, "_login_stage", "idle")

    async def expired_probe(force=False):
        return {
            "session_state": "expired",
            "authenticated": False,
            "checked_at": "2026-08-24T16:00:00+00:00",
            "error_code": "olx_session_expired",
            "error_message": "A OLX solicitou login novamente.",
            "probe_cache_age_seconds": 0.0,
        }

    monkeypatch.setattr(browser_worker, "_probe_olx_session", expired_probe)

    payload = await browser_worker._session_status_payload()

    assert payload["cookies_cached"] == 1
    assert payload["authenticated"] is False
    assert payload["session_state"] == "expired"
    assert payload["email"] == "buyer@example.com"
    assert payload["accounts"][0]["email"] == "buyer@example.com"


@pytest.mark.asyncio
async def test_session_status_reports_confirmed_account(monkeypatch, tmp_path):
    session_file = tmp_path / "storage_state.json"
    metadata_file = tmp_path / "session_metadata.json"
    session_file.write_text(json.dumps({"cookies": [{"name": "session"}]}), encoding="utf-8")
    metadata_file.write_text(
        json.dumps(
            {
                "email": "buyer@example.com",
                "authenticated_at": "2026-08-24T15:00:00+00:00",
                "last_verified_at": "2026-08-24T16:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(browser_worker, "SESSION_FILE", session_file)
    monkeypatch.setattr(browser_worker, "SESSION_METADATA_FILE", metadata_file)
    monkeypatch.setattr(browser_worker, "CDP_ENDPOINT", "")
    monkeypatch.setattr(browser_worker, "_login_stage", "idle")

    async def connected_probe(force=False):
        return {
            "session_state": "connected",
            "authenticated": True,
            "checked_at": "2026-08-24T16:00:00+00:00",
            "error_code": None,
            "error_message": "",
            "probe_cache_age_seconds": 0.0,
        }

    monkeypatch.setattr(browser_worker, "_probe_olx_session", connected_probe)

    payload = await browser_worker._session_status_payload()

    assert payload["authenticated"] is True
    assert payload["session_state"] == "connected"
    assert payload["accounts"] == [
        {
            "marketplace": "olx",
            "email": "buyer@example.com",
            "active": True,
            "authenticated": True,
            "authenticated_at": "2026-08-24T15:00:00+00:00",
            "last_verified_at": "2026-08-24T16:00:00+00:00",
        }
    ]


@pytest.mark.asyncio
async def test_session_status_reports_open_manual_login_without_probing(monkeypatch, tmp_path):
    monkeypatch.setattr(browser_worker, "SESSION_FILE", tmp_path / "storage_state.json")
    monkeypatch.setattr(browser_worker, "SESSION_METADATA_FILE", tmp_path / "session_metadata.json")
    monkeypatch.setattr(browser_worker, "CDP_ENDPOINT", "")
    monkeypatch.setattr(browser_worker, "_login_stage", "manual_login")
    monkeypatch.setattr(browser_worker, "_login_message", "Conclua o login no navegador.")

    async def should_not_probe(force=False):
        raise AssertionError("Manual login status must not probe before confirmation")

    monkeypatch.setattr(browser_worker, "_probe_olx_session", should_not_probe)

    payload = await browser_worker._session_status_payload()

    assert payload["authenticated"] is False
    assert payload["session_state"] == "manual_login"
    assert payload["login_message"] == "Conclua o login no navegador."


@pytest.mark.asyncio
async def test_session_status_idle_without_saved_session_reports_disconnected_and_worker_available(monkeypatch, tmp_path):
    monkeypatch.setattr(browser_worker, "SESSION_FILE", tmp_path / "storage_state.json")
    monkeypatch.setattr(browser_worker, "SESSION_METADATA_FILE", tmp_path / "session_metadata.json")
    monkeypatch.setattr(browser_worker, "CDP_ENDPOINT", "http://127.0.0.1:9222")
    monkeypatch.setattr(browser_worker, "_login_stage", "idle")
    monkeypatch.setattr(browser_worker, "_last_error_code", None)
    monkeypatch.setattr(browser_worker, "_last_error_message", "")
    browser_worker._invalidate_session_probe()

    async def cdp_not_ready():
        return False

    monkeypatch.setattr(browser_worker, "_cdp_ready", cdp_not_ready)

    payload = await browser_worker._session_status_payload()

    assert payload["authenticated"] is False
    assert payload["session_state"] == "disconnected"
    assert payload["worker_available"] is True
    assert payload["browser_ready"] is False
    assert payload["error_code"] is None
