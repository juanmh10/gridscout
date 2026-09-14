import pytest
from fastapi.testclient import TestClient
from apps.api import main as api_main
from apps.api.main import app
from apps.worker.jobs import process_one_job

@pytest.fixture(autouse=True, scope="module")
def isolated_db():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker
    from packages.core.database import Base, get_db
    import packages.core.seed as seed_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    orig_engine = seed_module.engine
    orig_session = seed_module.SessionLocal
    seed_module.engine = engine
    seed_module.SessionLocal = TestingSessionLocal
    try:
        seed_module.generate_seed()
    finally:
        seed_module.engine = orig_engine
        seed_module.SessionLocal = orig_session

    yield

    app.dependency_overrides.pop(get_db, None)

client = TestClient(app)


def _profile_headers():
    profiles = client.get("/api/v1/profiles")
    assert profiles.status_code == 200
    items = profiles.json()["items"]
    if not items:
        created = client.post("/api/v1/profiles", json={"name": "Perfil de teste"})
        assert created.status_code == 201
        items = [created.json()]
    return {"X-Profile-ID": items[0]["id"]}

def test_health_and_status():
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert "database" in res.json()

    res_status = client.get("/api/v1/status")
    assert res_status.status_code == 200
    data = res_status.json()
    assert data["app_mode"] == "local"
    assert data["synthetic_data"] is True
    assert data["external_providers"]["olx"] == "disabled"
    assert data["external_providers"]["vertex_ai"] == "disabled"
    assert data["external_providers"]["vertex_search"] == "disabled"

def test_dashboard_endpoint():
    res = client.get("/api/v1/dashboard", headers=_profile_headers())
    assert res.status_code == 200
    data = res.json()
    assert "active_listing_count" in data
    assert data["active_listing_count"] > 0
    assert "opportunity_count" in data
    assert "top_market_heat" in data
    assert len(data["top_market_heat"]) > 0
    assert "top_opportunities" in data
    assert len(data["top_opportunities"]) > 0
    assert "market_trend_summary" in data

def test_listings_endpoints():
    res = client.get("/api/v1/listings?page=1&page_size=10")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert len(data["items"]) == 10
    assert data["total"] >= 100
    
    first_id = data["items"][0]["id"]
    res_detail = client.get(f"/api/v1/listings/{first_id}")
    assert res_detail.status_code == 200
    detail = res_detail.json()
    assert detail["id"] == first_id
    assert "snapshots" in detail
    assert len(detail["snapshots"]) > 0

def test_products_and_market_endpoints():
    res = client.get("/api/v1/products")
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) > 0
    
    # The product catalog may contain reference-only products with no observed
    # listing yet. Market assertions need a product backed by market evidence.
    product = next(item for item in data["items"] if (item.get("active_listings_count") or 0) > 0)
    prod_id = product["id"]
    
    # Market statistics
    res_market = client.get(f"/api/v1/products/{prod_id}/market")
    assert res_market.status_code == 200
    market = res_market.json()
    assert market["product_id"] == prod_id
    assert "asking_median" in market
    assert "estimated_clearing_value" in market
    assert "market_heat" in market
    assert market["heat_band"] in ["COLD", "NORMAL", "HOT"]

    # Comparables
    res_comp = client.get(f"/api/v1/products/{prod_id}/comparables")
    assert res_comp.status_code == 200
    comps = res_comp.json()
    assert "comparables" in comps

    # History
    res_hist = client.get(f"/api/v1/products/{prod_id}/history")
    assert res_hist.status_code == 200
    hist = res_hist.json()
    assert "history" in hist
    assert len(hist["history"]) > 0

def test_products_advanced_filtering():
    # 1. Category filtering
    res_nb = client.get("/api/v1/products?category=notebook")
    assert res_nb.status_code == 200
    nb_data = res_nb.json()
    assert len(nb_data["items"]) > 0
    assert all(item["category"] == "notebook" for item in nb_data["items"])

    # 2. CPU filter >= Ryzen 5 5500U
    res_cpu = client.get("/api/v1/products?category=notebook&cpu_ge_5500u=true")
    assert res_cpu.status_code == 200
    cpu_data = res_cpu.json()
    assert len(cpu_data["items"]) > 0

    # 3. Price filter
    res_price = client.get("/api/v1/products?category=notebook&min_price=3000&max_price=6000")
    assert res_price.status_code == 200
    price_data = res_price.json()
    assert all(3000 <= (item["market_median_price"] or 0) <= 6000 for item in price_data["items"] if item["market_median_price"] > 0)

    # 4. Sort by price ascending and descending
    res_sort_asc = client.get("/api/v1/products?category=notebook&sort_by=price_asc&page_size=10")
    assert res_sort_asc.status_code == 200
    asc_items = res_sort_asc.json()["items"]
    asc_prices = [item["market_median_price"] for item in asc_items if item["market_median_price"] > 0]
    assert asc_prices == sorted(asc_prices)

    res_sort_desc = client.get("/api/v1/products?category=notebook&sort_by=price_desc&page_size=10")
    assert res_sort_desc.status_code == 200
    desc_items = res_sort_desc.json()["items"]
    desc_prices = [item["market_median_price"] for item in desc_items if item["market_median_price"] > 0]
    assert desc_prices == sorted(desc_prices, reverse=True)



def test_opportunities_endpoints():
    res = client.get("/api/v1/opportunities?page=1&page_size=5")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert len(data["items"]) > 0
    assert isinstance(data["items"][0]["source_url"], str)
    
    opp_id = data["items"][0]["id"]
    res_detail = client.get(f"/api/v1/opportunities/{opp_id}")
    assert res_detail.status_code == 200
    opp_detail = res_detail.json()
    assert opp_detail["id"] == opp_id
    assert "score_breakdown" in opp_detail
    assert "investigation" in opp_detail
    assert opp_detail["investigation"]["status"] == "completed"
    assert len(opp_detail["investigation"]["tool_trace"]) > 0

def test_pipelines_endpoints():
    headers = _profile_headers()
    res = client.get("/api/v1/pipelines", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert len(data["items"]) > 0

    # Trigger pipeline
    res_run = client.post("/api/v1/pipelines/run", headers=headers, json={"source_type": "fixture"})
    assert res_run.status_code == 202
    assert "pipeline_id" in res_run.json()



def test_model_metrics_and_marketplace_rate_limit_contracts():
    rate = client.get("/api/v1/marketplace/rate-limit")
    assert rate.status_code == 200
    assert rate.json()["limit_per_hour"] == 60
    assert rate.json()["min_interval_seconds"] == 8
    assert rate.json()["max_interval_seconds"] == 12

    summary = client.get("/api/v1/model-metrics/summary?window=24h")
    assert summary.status_code == 200
    assert summary.json()["currency"] == "USD"
    assert set(summary.json()["tokens"]) == {"prompt", "candidates", "thoughts", "cached", "tool", "total"}

    calls = client.get("/api/v1/model-metrics/calls?window=all&page=1&page_size=50")
    assert calls.status_code == 200
    assert {"items", "total", "page", "page_size", "pages"} <= set(calls.json())

def test_retrieval_endpoint():
    res = client.post("/api/v1/retrieval/search", json={"query": "RTX 3080 thermal pads", "limit": 3})
    assert res.status_code == 200
    data = res.json()
    assert "results" in data
    assert len(data["results"]) > 0
    assert data["results"][0]["score"] > 0.0

def test_benchmarks_endpoints():
    res = client.get("/api/v1/benchmarks")
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) > 0
    if not any("normalization_accuracy" in item["metrics"] for item in data["items"]):
        process_one_job()
        data = client.get("/api/v1/benchmarks").json()
    assert any("normalization_accuracy" in item["metrics"] for item in data["items"])

    # Trigger benchmark
    res_run = client.post("/api/v1/benchmarks/run", json={"dataset_version": "v1.0.0"})
    assert res_run.status_code == 202
    assert "benchmark_id" in res_run.json()

def test_settings_endpoints():
    headers = _profile_headers()
    res = client.get("/api/v1/settings", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "preference_profile" in data
    
    # Update settings
    updated = {"preference_profile": {"max_capital": 7500.0, "min_desired_edge": 0.15}}
    res_put = client.put("/api/v1/settings", headers=headers, json=updated)
    assert res_put.status_code == 200
    assert res_put.json()["preference_profile"]["max_capital"] == 7500.0

def test_marketplace_auth_flow(monkeypatch, tmp_path):
    # The real live flow is brokered by the visible browser-worker. Local mode
    # must reject it rather than accepting a fake or synthetic session.
    monkeypatch.setattr(api_main, "SESSION_FILE", str(tmp_path / "storage_state.json"))
    client.post("/api/v1/marketplace/auth/logout")
    res_req = client.post(
        "/api/v1/marketplace/auth/start?marketplace=olx",
        json={"email": "buyer@example.com"},
    )
    assert res_req.status_code == 409

    # No OTP endpoint or synthetic session is accepted in local mode.
    assert client.post("/api/v1/marketplace/auth/verify-code", json={"code": "123456"}).status_code == 409

    # Check the real storage-state status without creating credentials.
    res_status = client.get("/api/v1/marketplace/auth/status")
    assert res_status.status_code == 200
    status_data = res_status.json()
    assert status_data["authenticated"] is False
    assert status_data["human_emulation_active"] is False
    assert status_data["read_only_scope"] == ["search", "listing_detail"]
    assert status_data["login_stage"] == "idle"


def test_marketplace_auth_preserves_browser_error(monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")

    async def browser_error(*args, **kwargs):
        raise api_main.HTTPException(
            status_code=502,
            detail={
                "code": "olx_browser_unavailable",
                "message": "O Chrome visível da OLX está indisponível.",
            },
        )

    monkeypatch.setattr(api_main, "_browser_worker_request", browser_error)
    response = client.post(
        "/api/v1/marketplace/auth/start?marketplace=olx",
        json={"email": "buyer@example.com"},
    )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "olx_browser_unavailable"
    assert "Chrome" in response.json()["detail"]["message"]


def test_olx_pipeline_requires_a_confirmed_session(monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")

    async def expired_session(*args, **kwargs):
        return {
            "authenticated": False,
            "session_state": "expired",
            "error_code": "olx_session_expired",
            "error_message": "A OLX solicitou login novamente.",
        }

    monkeypatch.setattr(api_main, "_browser_worker_request", expired_session)
    response = client.post(
        "/api/v1/pipelines/run",
        headers=_profile_headers(),
        json={"source_type": "olx", "query": "rtx 3080"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "olx_session_required",
        "message": "A OLX solicitou login novamente.",
    }


def test_olx_pipeline_reports_unavailable_browser(monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")

    async def unavailable_session(*args, **kwargs):
        raise api_main.HTTPException(
            status_code=503,
            detail={
                "code": "olx_browser_worker_unavailable",
                "message": "O browser-worker da OLX está indisponível.",
            },
        )

    monkeypatch.setattr(api_main, "_browser_worker_request", unavailable_session)
    response = client.post(
        "/api/v1/pipelines/run",
        headers=_profile_headers(),
        json={"source_type": "olx", "query": "rtx 3080"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "olx_session_unavailable",
        "message": "O browser-worker da OLX está indisponível.",
    }


def test_manual_marketplace_login_endpoints_proxy_to_browser_worker(monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")
    requested_paths = []

    async def manual_browser_worker(method, path, payload=None):
        requested_paths.append((method, path, payload))
        return {
            "status": "manual_login" if path.endswith("start") else "authenticated",
            "message": "Login manual aberto." if path.endswith("start") else "Sessão confirmada.",
            "read_only": True,
            "error_code": None,
        }

    monkeypatch.setattr(api_main, "_browser_worker_request", manual_browser_worker)

    started = client.post("/api/v1/marketplace/auth/manual-start?marketplace=olx")
    completed = client.post("/api/v1/marketplace/auth/manual-complete?marketplace=olx")

    assert started.status_code == 200
    assert started.json()["status"] == "manual_login"
    assert completed.status_code == 200
    assert completed.json()["status"] == "authenticated"
    assert requested_paths == [
        ("POST", "/auth/manual-start", None),
        ("POST", "/auth/manual-complete", None),
    ]
