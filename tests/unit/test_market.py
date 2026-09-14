import pytest
from packages.market.engine import (
    compute_market_stats, compute_market_heat, 
    compute_opportunity_score, run_simulated_investigation
)

def test_market_stats_calculation():
    active_prices = [2500, 2700, 2800, 2900, 3100]
    disappeared_prices = [2200, 2400, 2500]
    durations = [3.0, 4.5, 2.0, 8.0, 12.0]
    
    stats = compute_market_stats(
        active_prices=active_prices,
        disappeared_prices=disappeared_prices,
        durations_days=durations,
        window_days=30
    )
    
    assert stats["sample_size"] == 8
    assert stats["active_count"] == 5
    assert stats["disappeared_count"] == 3
    assert stats["asking_median"] == 2800.0
    assert stats["median"] == 2600.0
    assert stats["p10"] < stats["p25"] <= stats["median"] <= stats["p75"] < stats["p90"]
    assert stats["mad"] >= 0
    assert stats["estimated_clearing_value"] < stats["asking_median"]
    assert stats["fast_sale_value"] <= stats["estimated_clearing_value"]
    assert stats["heat_band"] in ["COLD", "NORMAL", "HOT"]
    assert 0.0 <= stats["confidence"] <= 1.0


def test_market_stats_outlier_filtering():
    # Prices around 3000 with extreme trash outliers (R$ 10 and R$ 99999)
    active_prices = [10, 2900, 3000, 3100, 3200, 3300, 99999]
    disappeared_prices = [2800, 2950, 3050]
    
    stats = compute_market_stats(
        active_prices=active_prices,
        disappeared_prices=disappeared_prices,
        window_days=30
    )
    
    # Extreme outliers should be excluded from clean distribution
    assert stats["asking_median"] == 3100.0
    assert 2800 <= stats["median"] <= 3200
    assert stats["p90"] < 50000.0
    assert stats["p10"] > 100.0


def test_market_heat_bands():
    # Fast disappearance and high turnover -> HOT
    heat_hot, band_hot = compute_market_heat(active_count=4, disappeared_count=16, median_duration=2.5)
    assert heat_hot >= 70.0
    assert band_hot == "HOT"

    # Moderate turnover -> NORMAL
    heat_norm, band_norm = compute_market_heat(active_count=10, disappeared_count=10, median_duration=6.0)
    assert 35.0 <= heat_norm < 70.0
    assert band_norm == "NORMAL"

    # Slow turnover -> COLD
    heat_cold, band_cold = compute_market_heat(active_count=20, disappeared_count=2, median_duration=15.0)
    assert heat_cold < 35.0
    assert band_cold == "COLD"

def test_opportunity_score_components():
    stats = {
        "estimated_clearing_value": 3000.0,
        "market_heat": 85.0,
        "category": "gpu"
    }
    prefs = {
        "target_categories": ["gpu"],
        "category_expertise": {"gpu": 1.0},
        "max_capital": 5000.0,
        "min_desired_edge": 0.10
    }
    
    # 25% edge listing (asking 2250 on 3000 clearing)
    score, breakdown, edge, explanation = compute_opportunity_score(
        listing_price=2250.0,
        market_stats=stats,
        preferences=prefs,
        condition="like_new",
        freshness_days=1.0,
        is_preferred_location=True
    )
    
    assert edge == 0.25
    assert score >= 75.0
    assert breakdown["price_edge_component"] > 0
    assert breakdown["liquidity_component"] > 0
    assert breakdown["condition_component"] == 15.0
    assert breakdown["personal_fit_component"] == 15.0
    assert breakdown["risk_penalty"] == 0.0
    assert "below estimated clearing value" in explanation

def test_opportunity_score_scam_penalty():
    stats = {
        "estimated_clearing_value": 3000.0,
        "market_heat": 80.0,
        "category": "gpu"
    }
    prefs = {"category_expertise": {"gpu": 1.0}, "max_capital": 5000.0}
    
    # Suspiciously cheap (75% discount -> price 750)
    score, breakdown, edge, expl = compute_opportunity_score(
        listing_price=750.0,
        market_stats=stats,
        preferences=prefs,
        risk_factors=["whatsapp_scam"]
    )
    assert breakdown["risk_penalty"] >= 35.0

def test_simulated_investigation():
    listing = {"price": 2200.0, "location_city": "Campinas", "location_state": "SP", "condition": "like_new"}
    prod = {"id": "prod-gpu-rtx3080", "display_name": "NVIDIA GeForce RTX 3080 10GB"}
    stats = {"estimated_clearing_value": 2800.0, "median": 2900.0, "sample_size": 25, "heat_band": "HOT", "confidence": 0.94}
    knowledge = [{"title": "VRAM inspection", "body": "Check GDDR6X thermal pads."}]

    inv = run_simulated_investigation(listing, prod, stats, knowledge)
    assert inv["status"] == "completed"
    assert inv["verdict"] == "ATTRACTIVE_OPPORTUNITY"
    assert len(inv["key_evidence"]) >= 3
    assert len(inv["risks"]) >= 1
    assert len(inv["tool_trace"]) >= 2

def test_cpu_tier_comparison_with_ryzen_5_5500u():
    from apps.api.main import is_cpu_ge_5500u

    # Base and better CPUs:
    assert is_cpu_ge_5500u("AMD", "Ryzen 5 5500U") is True
    assert is_cpu_ge_5500u("AMD", "Ryzen 5 7530U") is True
    assert is_cpu_ge_5500u("AMD", "Ryzen 7 5700U") is True
    assert is_cpu_ge_5500u("Intel", "Core i5-12450H") is True
    assert is_cpu_ge_5500u("Intel", "Core i7-13700H") is True
    assert is_cpu_ge_5500u("Intel", "Core Ultra 7 155H") is True
    assert is_cpu_ge_5500u("Apple", "Apple M1 (8-core)") is True
    assert is_cpu_ge_5500u("Qualcomm", "Snapdragon X Elite X1E-78-100") is True

    # Weaker CPUs (below Ryzen 5 5500U):
    assert is_cpu_ge_5500u("Intel", "Celeron N4000") is False
    assert is_cpu_ge_5500u("Intel", "Intel Processor N100") is False
    assert is_cpu_ge_5500u("Intel", "Core i3-1115G4") is False
    assert is_cpu_ge_5500u("Intel", "Core i3-1215U") is False
    assert is_cpu_ge_5500u("Intel", "Core i5-1135G7") is False
    assert is_cpu_ge_5500u("Intel", "Core i7-10510U") is False
    assert is_cpu_ge_5500u("AMD", "Ryzen 3 7330U") is False
    assert is_cpu_ge_5500u("AMD", "Ryzen 5 7520U") is False

