import datetime as dt

from packages.ai.metrics import estimate_cost


def test_published_gemini_prices_include_thoughts_as_output():
    usage = {"prompt_tokens": 1_000_000, "candidates_tokens": 1_000_000, "thoughts_tokens": 1_000_000, "cached_tokens": 1_000_000}
    cost, version = estimate_cost("gemini-3.7-flash", dt.datetime(2026, 8, 24), usage)
    assert cost == 8.325
    assert version == "gemini-3.7-flash-paid-standard-2026"


def test_gemini_37_price_changes_in_2027_and_unknown_is_null():
    usage = {"prompt_tokens": 1_000_000, "candidates_tokens": 0, "thoughts_tokens": 0, "cached_tokens": 0}
    assert estimate_cost("gemini-3.7-flash", dt.datetime(2027, 1, 1), usage) == (1.5, "gemini-3.7-flash-paid-standard-2027")
    assert estimate_cost("gemini-2.5-flash", dt.datetime(2026, 8, 24), usage) == (None, None)
