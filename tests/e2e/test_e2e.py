import pytest
from fastapi.testclient import TestClient
from apps.api.main import app
from packages.core.seed import generate_seed
from packages.core.database import SessionLocal
from packages.core.models import Listing, MarketSnapshot, Opportunity, PipelineRun, EvalRun
from apps.worker.jobs import process_one_job

def test_complete_acceptance_scenario():
    """
    Validates the end-to-end acceptance scenario described in GOAL.md:
    1. Load deterministic fixtures / Seed
    2. Run pipeline
    3. Listing snapshots persisted & products normalized
    4. Used comparables selected & market snapshots calculated
    5. Opportunities scored & local simulated investigation executed
    6. Dashboard displays persisted results
    7. Open opportunity detail with score breakdown & investigation
    8. Open market analysis with robust stats & heat band
    9. Run/view benchmark suite
    10. Verify data persistence across session
    11. Confirm no external authentication was attempted
    """
    # 1. Load deterministic fixture
    generate_seed()
    client = TestClient(app)
    profiles = client.get("/api/v1/profiles")
    assert profiles.status_code == 200
    profile_items = profiles.json()["items"]
    assert profile_items
    profile_headers = {"X-Profile-ID": profile_items[0]["id"]}

    # Verify status in local mode
    status_res = client.get("/api/v1/status")
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["app_mode"] == "local"
    assert status_data["synthetic_data"] is True
    assert status_data["external_providers"]["olx"] == "disabled"
    assert status_data["external_providers"]["vertex_ai"] == "disabled"
    assert status_data["external_providers"]["gemini_embeddings"] == "disabled"

    # 2. Trigger pipeline run
    pipe_res = client.post("/api/v1/pipelines/run", headers=profile_headers, json={"source_type": "fixture"})
    assert pipe_res.status_code == 202
    pipe_data = pipe_res.json()
    assert "pipeline_id" in pipe_data
    # The acceptance database is persistent and can contain queued work from
    # earlier local runs. Drain a bounded backlog instead of assuming this
    # pipeline is one of the first five jobs.
    pipe_status = None
    for _ in range(100):
        process_one_job()
        db_check = SessionLocal()
        try:
            pipe_status = db_check.query(PipelineRun).filter(PipelineRun.id == pipe_data["pipeline_id"]).first()
            if pipe_status and pipe_status.status in {"completed", "failed"}:
                break
        finally:
            db_check.close()
    assert pipe_status is not None
    assert pipe_status.status == "completed"

    # 3. Verify listing snapshots and products
    listings_res = client.get("/api/v1/listings?page=1&page_size=20&status=all")
    assert listings_res.status_code == 200
    listings_data = listings_res.json()
    assert listings_data["total"] >= 150
    assert len(listings_data["items"]) == 20

    first_listing_id = listings_data["items"][0]["id"]
    detail_res = client.get(f"/api/v1/listings/{first_listing_id}")
    assert detail_res.status_code == 200
    assert len(detail_res.json()["snapshots"]) > 0

    # 4. Open Market analysis for a product
    prod_res = client.get("/api/v1/products")
    assert prod_res.status_code == 200
    products = prod_res.json()["items"]
    target_prod = next((p for p in products if (p.get("active_listings_count") or 0) > 0), products[0])
    target_prod_id = target_prod["id"]
    market_res = client.get(f"/api/v1/products/{target_prod_id}/market")
    assert market_res.status_code == 200
    market_data = market_res.json()
    assert market_data["sample_size"] > 0
    assert market_data["asking_median"] > 0
    assert market_data["estimated_clearing_value"] > 0
    assert market_data["fast_sale_value"] <= market_data["estimated_clearing_value"]
    assert market_data["heat_band"] in ["COLD", "NORMAL", "HOT"]

    # Check comparables
    comps_res = client.get(f"/api/v1/products/{target_prod_id}/comparables")
    assert comps_res.status_code == 200
    assert len(comps_res.json()["comparables"]) > 0

    # 5. Open Opportunities
    opps_res = client.get("/api/v1/opportunities?page=1&page_size=10")
    assert opps_res.status_code == 200
    opps_data = opps_res.json()
    assert len(opps_data["items"]) > 0
    assert opps_data["items"][0]["final_score"] >= 60.0

    target_opp_id = opps_data["items"][0]["id"]
    opp_detail_res = client.get(f"/api/v1/opportunities/{target_opp_id}")
    assert opp_detail_res.status_code == 200
    opp_detail = opp_detail_res.json()
    assert "score_breakdown" in opp_detail
    assert "price_edge_component" in opp_detail["score_breakdown"]
    assert "liquidity_component" in opp_detail["score_breakdown"]
    assert "investigation" in opp_detail
    assert opp_detail["investigation"]["status"] == "completed"
    assert len(opp_detail["investigation"]["tool_trace"]) > 0

    # 6. Dashboard displays persisted results
    dash_res = client.get("/api/v1/dashboard", headers=profile_headers)
    assert dash_res.status_code == 200
    dash = dash_res.json()
    assert dash["active_listing_count"] > 0
    assert dash["opportunity_count"] > 0
    assert len(dash["top_market_heat"]) > 0
    assert len(dash["top_opportunities"]) > 0

    # 7. Run/view Benchmarks
    bench_res = client.get("/api/v1/benchmarks")
    assert bench_res.status_code == 200
    bench_items = bench_res.json()["items"]
    assert len(bench_items) > 0
    if not any("normalization_accuracy" in item["metrics"] for item in bench_items):
        bench_run = client.post("/api/v1/benchmarks/run", json={"dataset_version": "v1.0.0"})
        assert bench_run.status_code == 202
        process_one_job()
        bench_items = client.get("/api/v1/benchmarks").json()["items"]
    completed_benchmark = next(item for item in bench_items if "normalization_accuracy" in item["metrics"])
    assert completed_benchmark["metrics"]["normalization_accuracy"] >= 0.90

    # 8. Verify persistence across fresh database session
    db = SessionLocal()
    try:
        assert db.query(Listing).count() >= 150
        assert db.query(MarketSnapshot).count() >= 8
        assert db.query(Opportunity).count() > 0
        assert db.query(PipelineRun).count() > 0
        assert db.query(EvalRun).count() > 0
    finally:
        db.close()
