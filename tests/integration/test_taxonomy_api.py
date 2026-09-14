import pytest
from fastapi.testclient import TestClient
from apps.api.main import app
from packages.core.seed import generate_seed


@pytest.fixture(scope="module")
def seeded_client():
    generate_seed()
    return TestClient(app)


def test_api_categories_endpoint(seeded_client):
    response = seeded_client.get("/api/v1/products/categories")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert len(data["items"]) > 0
    cat_ids = [item["id"] for item in data["items"]]
    assert "gpu" in cat_ids or "notebook" in cat_ids or "cpu" in cat_ids


def test_api_facets_endpoint_valid_and_invalid(seeded_client):
    # Valid category
    resp_gpu = seeded_client.get("/api/v1/products/facets?category=gpu")
    assert resp_gpu.status_code == 200
    data_gpu = resp_gpu.json()
    assert data_gpu["category"] == "gpu"
    assert "facets" in data_gpu
    facet_keys = [f["key"] for f in data_gpu["facets"]]
    assert "vram_gb" in facet_keys or "chipset" in facet_keys

    # Invalid category (must return 422)
    resp_invalid = seeded_client.get("/api/v1/products/facets?category=invalid_category_xyz")
    assert resp_invalid.status_code == 422


def test_api_taxonomy_audit_endpoint(seeded_client):
    response = seeded_client.get("/api/v1/audit/taxonomy")
    assert response.status_code == 200
    data = response.json()
    assert "summary" in data
    assert "category_breakdown" in data
    assert "data_quality_diagnostics" in data
