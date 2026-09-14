import pytest

from packages.marketplace.olx_guard import OlxGuardError, OlxNavigationGuard


@pytest.mark.asyncio
async def test_preflight_and_cooldown_are_persisted(tmp_path):
    state_file = tmp_path / "rate_limit_state.json"
    guard = OlxNavigationGuard(state_file)
    ready = await guard.preflight(60)
    assert ready["remaining"] == 60

    await guard.cooldown()
    restarted = OlxNavigationGuard(state_file)
    status = await restarted.status()
    assert status["state"] == "cooldown"
    with pytest.raises(OlxGuardError) as error:
        await restarted.preflight(1)
    assert error.value.code == "olx_circuit_open"
    assert error.value.detail["retry_after_seconds"] > 0
