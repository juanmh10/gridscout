"""Serialized, persisted safety guard for read-only OLX browser navigation."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import random
import time
from pathlib import Path
from typing import Any

LIMIT_PER_HOUR = 60
WINDOW_SECONDS = 60 * 60
COOLDOWN_SECONDS = 60 * 60
MIN_INTERVAL_SECONDS = 8
MAX_INTERVAL_SECONDS = 12


class OlxGuardError(Exception):
    def __init__(self, code: str, message: str, *, retry_after_seconds: int, reset_at: str, remaining: int, status_code: int = 429):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.detail = {
            "message": message,
            "retry_after_seconds": retry_after_seconds,
            "reset_at": reset_at,
            "remaining": remaining,
        }


class OlxNavigationGuard:
    def __init__(self, state_file: Path):
        self.state_file = state_file
        self._lock = asyncio.Lock()

    @staticmethod
    def _iso(timestamp: float) -> str:
        return dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc).isoformat()

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, state: dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.state_file)
        os.chmod(self.state_file, 0o600)

    def _normalise(self, state: dict[str, Any], now: float) -> dict[str, Any]:
        navigations = [float(value) for value in state.get("navigations", []) if float(value) > now - WINDOW_SECONDS]
        return {
            "navigations": navigations,
            "last_navigation_at": float(state.get("last_navigation_at", 0) or 0),
            "cooldown_until": float(state.get("cooldown_until", 0) or 0),
        }

    def _payload(self, state: dict[str, Any], now: float) -> dict[str, Any]:
        used = len(state["navigations"])
        cooldown_until = state["cooldown_until"]
        reset_timestamp = (
            cooldown_until if cooldown_until > now else
            state["navigations"][0] + WINDOW_SECONDS if state["navigations"] else now
        )
        retry = max(0, int(round(reset_timestamp - now)))
        return {
            "state": "cooldown" if cooldown_until > now else "closed",
            "limit_per_hour": LIMIT_PER_HOUR,
            "used": used,
            "remaining": max(0, LIMIT_PER_HOUR - used),
            "reset_at": self._iso(reset_timestamp),
            "retry_after_seconds": retry,
            "min_interval_seconds": MIN_INTERVAL_SECONDS,
            "max_interval_seconds": MAX_INTERVAL_SECONDS,
        }

    async def status(self) -> dict[str, Any]:
        async with self._lock:
            now = time.time()
            state = self._normalise(self._read(), now)
            self._write(state)
            return self._payload(state, now)

    def _raise_if_unavailable(self, state: dict[str, Any], now: float, needed: int = 1) -> None:
        payload = self._payload(state, now)
        if payload["state"] == "cooldown":
            raise OlxGuardError(
                "olx_circuit_open", "A proteção da OLX está em cooldown após bloqueio ou desafio.",
                retry_after_seconds=payload["retry_after_seconds"], reset_at=payload["reset_at"], remaining=payload["remaining"],
            )
        if payload["remaining"] < needed:
            raise OlxGuardError(
                "olx_budget_exhausted", "O orçamento horário de navegações OLX foi esgotado.",
                retry_after_seconds=payload["retry_after_seconds"], reset_at=payload["reset_at"], remaining=payload["remaining"],
            )

    async def preflight(self, needed: int) -> dict[str, Any]:
        async with self._lock:
            now = time.time()
            state = self._normalise(self._read(), now)
            self._raise_if_unavailable(state, now, max(1, needed))
            self._write(state)
            return self._payload(state, now)

    async def navigate(self, page, url: str, **kwargs):
        """The only path for automated read navigation; it serializes the full goto."""
        async with self._lock:
            now = time.time()
            state = self._normalise(self._read(), now)
            self._raise_if_unavailable(state, now)
            elapsed = now - state["last_navigation_at"]
            if state["last_navigation_at"] and elapsed < MIN_INTERVAL_SECONDS:
                await asyncio.sleep(random.uniform(MIN_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS) - elapsed)
                now = time.time()
                state = self._normalise(self._read(), now)
                self._raise_if_unavailable(state, now)
            state["navigations"].append(now)
            state["last_navigation_at"] = now
            self._write(state)
            response = await page.goto(url, **kwargs)
            if response is not None and response.status in {403, 429}:
                await self._cooldown_locked(state, time.time())
                payload = self._payload(state, time.time())
                raise OlxGuardError(
                    "olx_access_blocked", "A OLX recusou a navegação; o worker entrou em cooldown sem retry automático.",
                    retry_after_seconds=payload["retry_after_seconds"], reset_at=payload["reset_at"], remaining=payload["remaining"],
                )
            return response

    async def cooldown(self) -> dict[str, Any]:
        async with self._lock:
            now = time.time()
            state = self._normalise(self._read(), now)
            await self._cooldown_locked(state, now)
            return self._payload(state, now)

    async def _cooldown_locked(self, state: dict[str, Any], now: float) -> None:
        state["cooldown_until"] = max(state.get("cooldown_until", 0), now + COOLDOWN_SECONDS)
        self._write(state)
