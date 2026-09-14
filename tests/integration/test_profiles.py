import datetime as dt
import os
from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import apps.api.main as api_main
from apps.api.main import app
from packages.core.database import Base, get_db
from packages.core.models import Listing, ListingDiscovery, ListingEnrichmentTask, Opportunity, OpportunitySignal, PipelineRun, Product
from packages.pipeline.runner import _default_preferences
from packages.pipeline.card_projection import project_run_cards
from packages.market.engine import calculate_market_snapshot_from_snapshots
from packages.search import compile_search_local


@pytest.fixture()
def api_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, testing_session
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _create_profile(client: TestClient, name: str, preferences=None) -> dict:
    response = client.post(
        "/api/v1/profiles",
        json={"name": name, "preferences": preferences or {}},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_profiles_require_headers_normalize_names_and_isolate_settings(api_client):
    client, session_factory = api_client
    assert client.get("/api/v1/settings").json()["detail"]["code"] == "PROFILE_REQUIRED"
    assert client.get("/api/v1/dashboard").json()["detail"]["code"] == "PROFILE_REQUIRED"

    first = _create_profile(client, "  Ána   Silva ")
    conflict = client.post("/api/v1/profiles", json={"name": "ana silva"})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "PROFILE_NAME_CONFLICT"
    second = _create_profile(client, "Bruno")
    updated_first = client.patch(f"/api/v1/profiles/{first['id']}", json={"preferences": {"icon": "zap", "color": "emerald"}})
    assert updated_first.status_code == 200
    assert updated_first.json()["preferences"] == {"icon": "zap", "color": "emerald"}
    first_headers = {"X-Profile-ID": first["id"]}
    second_headers = {"X-Profile-ID": second["id"]}

    assert client.put(
        "/api/v1/settings", headers=first_headers,
        json={"preference_profile": {"max_capital": 1000}},
    ).status_code == 200
    assert client.put(
        "/api/v1/settings", headers=second_headers,
        json={"preference_profile": {"max_capital": 2000}},
    ).status_code == 200
    assert client.get("/api/v1/settings", headers=first_headers).json()["preference_profile"] == {"max_capital": 1000}
    assert client.get("/api/v1/settings", headers=second_headers).json()["preference_profile"] == {"max_capital": 2000}
    db = session_factory()
    assert _default_preferences(db, first["id"])["max_capital"] == 1000
    assert _default_preferences(db, second["id"])["max_capital"] == 2000
    db.close()

    definition = client.post(
        "/api/v1/search-definitions", headers=first_headers,
        json={"name": "RTX", "intent": {"query": "rtx 3080"}, "plan": {"source_type": "fixture", "query": "rtx 3080"}},
    )
    assert definition.status_code == 201
    forbidden = client.get(f"/api/v1/search-definitions/{definition.json()['id']}", headers=second_headers)
    assert forbidden.status_code == 404
    assert forbidden.json()["detail"]["code"] == "RESOURCE_NOT_FOUND"

    scope = client.post(
        "/api/v1/search-scopes", headers=first_headers,
        json={"name": "Fixture RTX", "marketplace": "fixture", "query": "rtx 3080"},
    )
    assert scope.status_code == 201
    assert client.get("/api/v1/search-scopes?marketplace=fixture", headers=second_headers).json()["items"] == []

    run = client.post("/api/v1/pipelines/run", headers=first_headers, json={"source_type": "fixture"})
    assert run.status_code == 202
    denied_run = client.get(f"/api/v1/pipelines/{run.json()['pipeline_id']}", headers=second_headers)
    assert denied_run.status_code == 404
    assert denied_run.json()["detail"]["code"] == "RESOURCE_NOT_FOUND"
    assert client.get("/api/v1/dashboard", headers=first_headers).json()["pipeline_runs_count"] == 1
    second_dashboard = client.get("/api/v1/dashboard", headers=second_headers).json()
    assert second_dashboard["pipeline_runs_count"] == 0
    assert second_dashboard["recent_pipelines"] == []


def test_high_volume_pipeline_accepts_broad_collection_and_exposes_safe_progress(api_client):
    client, _ = api_client
    profile = _create_profile(client, "Carga alta")
    headers = {"X-Profile-ID": profile["id"]}

    unbounded = client.post(
        "/api/v1/pipelines/run",
        headers=headers,
        json={"source_type": "fixture", "workload_mode": "high_volume"},
    )
    assert unbounded.status_code == 422
    assert unbounded.json()["detail"]["code"] == "HIGH_VOLUME_QUERY_REQUIRED"

    preflight = client.post(
        "/api/v1/pipelines/high-volume/preflight",
        headers=headers,
        json={
            "source_type": "fixture",
            "query": "RTX",
            "duration_minutes": 30,
            "aggressiveness": "intensive",
        },
    )
    assert preflight.status_code == 200
    assert preflight.json()["can_start"] is True
    assert preflight.json()["duration_minutes"] == 30
    assert preflight.json()["aggressiveness"] == "intensive"
    assert preflight.json()["navigation_cap"] == 27
    assert preflight.json()["estimated_discoveries"] == {"min": 600, "max": 900}
    assert preflight.json()["estimated_details_max"] == 0
    assert preflight.json()["detail_access_mode"] == "disabled"

    queued = client.post(
        "/api/v1/pipelines/run",
        headers=headers,
        json={
            "source_type": "fixture", "query": "RTX", "workload_mode": "high_volume",
            "duration_minutes": 30, "aggressiveness": "intensive",
        },
    )
    assert queued.status_code == 202, queued.text
    pipeline = client.get(f"/api/v1/pipelines/{queued.json()['pipeline_id']}", headers=headers).json()
    assert pipeline["workload"]["mode"] == "high_volume"
    assert pipeline["workload"]["discovery_target"] == 2000
    assert pipeline["workload"]["duration_minutes"] == 30
    assert pipeline["workload"]["aggressiveness"] == "intensive"
    assert pipeline["workload"]["estimated_discoveries"] == {"min": 600, "max": 900}
    assert pipeline["workload"]["detail_access_mode"] == "disabled"
    assert pipeline["workload"]["enrichment_threshold"] == 70
    assert "task_id" not in pipeline["workload"]
    assert pipeline["steps"][0]["configuration"]["search_definition_id"] is None
    assert pipeline["steps"][0]["configuration"]["scopes"][0]["query"] == "RTX"


def test_optional_card_agent_is_preflighted_and_persisted(api_client, monkeypatch):
    client, _ = api_client
    profile = _create_profile(client, "Agente de cards")
    headers = {"X-Profile-ID": profile["id"]}
    payload = {
        "source_type": "fixture", "query": "PlayStation 5", "workload_mode": "high_volume",
        "card_review_mode": "required",
    }

    monkeypatch.setattr(api_main, "_card_agent_available", lambda: False)
    forecast = client.post("/api/v1/pipelines/high-volume/preflight", headers=headers, json=payload)
    assert forecast.status_code == 200
    assert forecast.json()["can_start"] is False
    assert forecast.json()["card_agent_available"] is False
    blocked = client.post("/api/v1/pipelines/run", headers=headers, json=payload)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "CARD_AGENT_UNAVAILABLE"

    monkeypatch.setattr(api_main, "_card_agent_available", lambda: True)
    queued = client.post("/api/v1/pipelines/run", headers=headers, json=payload)
    assert queued.status_code == 202
    pipeline = client.get(f"/api/v1/pipelines/{queued.json()['pipeline_id']}", headers=headers).json()
    assert pipeline["workload"]["card_review_mode"] == "required"


def test_opportunity_category_filter_uses_canonical_product_category(api_client):
    client, session_factory = api_client
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    db = session_factory()
    try:
        notebook = Product(
            id="notebook-filter-product", category="notebook", brand="Dell", family="Latitude",
            model="5420", variant=None, display_name="Dell Latitude 5420", attributes={},
            canonical_tier=1, created_at=now,
        )
        notebook_listing = Listing(
            id="notebook-filter-listing", source="fixture", external_id="notebook-filter-listing",
            product_id=notebook.id, title="Notebook Dell Latitude 5420", price=2300.0, created_at=now,
        )
        # Simulates rows written before the denormalized category was fixed.
        stale_opportunity = Opportunity(
            id="notebook-filter-opportunity", listing_id=notebook_listing.id, product_id=notebook.id,
            category="gpu", final_score=80.0, computed_at=now,
        )
        other = Product(
            id="other-filter-product", category="other", brand="Canon", family="EOS",
            model="Rebel", variant=None, display_name="Canon EOS Rebel", attributes={},
            canonical_tier=2, created_at=now,
        )
        other_listing = Listing(
            id="other-filter-listing", source="fixture", external_id="other-filter-listing",
            product_id=other.id, title="Câmera Canon", price=1200.0, created_at=now,
        )
        other_opportunity = Opportunity(
            id="other-filter-opportunity", listing_id=other_listing.id, product_id=other.id,
            category="other", final_score=99.0, computed_at=now,
        )
        db.add_all([notebook, notebook_listing, stale_opportunity, other, other_listing, other_opportunity])
        db.commit()
    finally:
        db.close()

    notebook_response = client.get("/api/v1/opportunities?category=notebook")
    assert notebook_response.status_code == 200
    assert [item["id"] for item in notebook_response.json()["items"]] == ["notebook-filter-opportunity"]
    assert notebook_response.json()["items"][0]["category"] == "notebook"

    other_market = client.get("/api/v1/products/other-filter-product/market")
    assert other_market.status_code == 200
    assert other_market.json()["market_eligible"] is True
    assert other_market.json().get("market_exclusion_reason") is None


def test_preliminary_opportunity_is_profile_scoped_and_explicitly_incomplete(api_client):
    client, session_factory = api_client
    profile = _create_profile(client, "Sinais preliminares")
    headers = {"X-Profile-ID": profile["id"]}
    db = session_factory()
    run = PipelineRun(id="signal-run", profile_id=profile["id"], status="completed", workload_mode="high_volume", steps=[{}])
    discovery = ListingDiscovery(
        id="signal-discovery", pipeline_run_id=run.id, canonical_key="signal-card",
        external_id="signal-card", title="Notebook Dell Latitude 5420", price=2300,
        location="Campinas, SP", normalized_url="https://example.test/card",
        card_analysis_status="completed",
        card_analysis_result={"category": "notebook", "brand": "Dell", "model": "Latitude 5420", "confidence": 0.9},
    )
    signal = OpportunitySignal(
        id="signal-public", profile_id=profile["id"], pipeline_run_id=run.id,
        listing_discovery_id=discovery.id, stage="preliminary", preliminary_score=91,
        full_flow_completed=False,
    )
    db.add_all([run, discovery, signal])
    db.commit()
    db.close()

    response = client.get("/api/v1/opportunities?stage=preliminary", headers=headers)
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["stage"] == "preliminary"
    assert item["preliminary_score"] == 91
    assert item["final_score"] is None
    assert item["full_flow_completed"] is False


def test_card_observations_are_visible_and_market_is_explicitly_preliminary(api_client):
    client, session_factory = api_client
    profile = _create_profile(client, "Cards visíveis")
    db = session_factory()
    try:
        run = PipelineRun(id="projection-run", profile_id=profile["id"], status="completed", workload_mode="high_volume")
        cards = [
            ListingDiscovery(
                id=f"projection-card-{index}", pipeline_run_id=run.id, canonical_key=f"projection-{index}",
                external_id=f"projection-{index}", normalized_url=f"https://www.olx.com.br/item/{index}",
                title=title, price=price, triage_status="detail_candidate",
            )
            for index, (title, price) in enumerate([
                ("PS5 Slim", 3200), ("PlayStation 5", 3300), ("Ps5 usada", 3100),
                ("PLAYSTATION 5", 3400), ("PS5 digital", 3000),
            ])
        ]
        db.add_all([run, *cards])
        db.commit()
        project_run_cards(db, run.id)
        product_id = db.query(Listing).filter(Listing.source == "olx").first().product_id
        calculate_market_snapshot_from_snapshots(
            db, product_id=product_id, category="other", source="olx", evidence_tier="preliminary",
        )
        db.commit()
    finally:
        db.close()

    listings = client.get("/api/v1/listings", params={"audit_status": "unaudited"})
    assert listings.status_code == 200
    assert listings.json()["total"] == 5
    assert {item["audit_status"] for item in listings.json()["items"]} == {"unaudited"}
    assert all(item["price"] is not None for item in listings.json()["items"])

    market = client.get(f"/api/v1/products/{product_id}/market")
    assert market.status_code == 200
    preliminary = market.json()["preliminary_market"]
    assert preliminary["included_count"] == 5
    assert market.json()["included_count"] == 0


def test_incomplete_discoveries_are_profile_scoped_and_exclude_completed_detail_cards(api_client, monkeypatch):
    client, session_factory = api_client
    profile = _create_profile(client, "Dados incompletos")
    headers = {"X-Profile-ID": profile["id"]}
    db = session_factory()
    try:
        run = PipelineRun(
            id="incomplete-run",
            profile_id=profile["id"],
            status="completed_partial",
            workload_mode="high_volume",
        )
        incomplete = ListingDiscovery(
            id="incomplete-card",
            pipeline_run_id=run.id,
            canonical_key="olx:card",
            external_id="https://example.test/card",
            normalized_url="https://example.test/card",
            title="Notebook Dell Latitude 5420 <br>",
            price=2500.0,
            card_analysis_status="completed",
            card_analysis_result={"category": "notebook", "brand": "Dell", "model": "Latitude 5420", "confidence": 0.88},
        )
        completed = ListingDiscovery(
            id="detailed-card",
            pipeline_run_id=run.id,
            canonical_key="olx:detailed",
            external_id="https://example.test/detailed",
            title="Com detalhe confirmado",
        )
        task = ListingEnrichmentTask(
            id="completed-detail",
            pipeline_run_id=run.id,
            listing_discovery_id=completed.id,
            status="completed",
        )
        db.add_all([run, incomplete, completed, task])
        db.commit()
    finally:
        db.close()

    response = client.get("/api/v1/incomplete-discoveries", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["total"] == 1
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["title"] == "Notebook Dell Latitude 5420"
    assert item["analysis"]["model_name"] == "Latitude 5420"
    assert "id" not in item
    assert item["evidence_scope"].startswith("Somente card")

    monkeypatch.setattr(api_main, "queue_latest_incomplete_cards", lambda profile_id: {"run_id": "incomplete-run", "queued": 1, "total": 1})
    queued = client.post("/api/v1/incomplete-discoveries/analyze", headers=headers)
    assert queued.status_code == 200
    assert queued.json()["queued"] == 1


def test_session_prepares_pipeline_scope_and_feedback_is_per_profile(api_client):
    client, session_factory = api_client
    first = _create_profile(client, "A")
    second = _create_profile(client, "B")
    first_headers = {"X-Profile-ID": first["id"]}
    second_headers = {"X-Profile-ID": second["id"]}

    created = client.post(
        "/api/v1/search-sessions", headers=first_headers,
        json={"draft": {"intent": {"query": "rtx 3080"}, "plan": {"source_type": "fixture", "query": "rtx 3080"}}},
    )
    assert created.status_code == 201
    session_id = created.json()["id"]
    assert {"intent", "plan", "status", "clarification"} <= set(created.json())
    first_message = client.post(
        f"/api/v1/search-sessions/{session_id}/messages", headers=first_headers,
        json={"client_request_id": "message-1", "content": "até R$ 3.000"},
    )
    assert first_message.status_code == 200
    message_replay = client.post(
        f"/api/v1/search-sessions/{session_id}/messages", headers=first_headers,
        json={"client_request_id": "message-1", "content": "não deve recompilar"},
    )
    assert message_replay.status_code == 200
    assert len(message_replay.json()["messages"]) == 1
    assert message_replay.json()["messages"][0]["content"] == "até R$ 3.000"
    confirmed = client.post(f"/api/v1/search-sessions/{session_id}/confirm", headers=first_headers)
    assert confirmed.status_code == 200, confirmed.text
    legacy_execute = client.post(f"/api/v1/search-sessions/{session_id}/execute", headers=first_headers)
    assert legacy_execute.status_code == 410
    assert legacy_execute.json()["detail"]["code"] == "PIPELINE_EXECUTION_REQUIRES_PIPELINES_SCREEN"
    saved = client.post(f"/api/v1/search-sessions/{session_id}/save", headers=first_headers)
    assert saved.status_code == 201, saved.text
    definition_id = saved.json()["definition"]["id"]
    executed = client.post(
        "/api/v1/pipelines/run", headers=first_headers,
        json={"source_type": "fixture", "search_definition_id": definition_id},
    )
    assert executed.status_code == 202, executed.text
    pipeline = client.get(f"/api/v1/pipelines/{executed.json()['pipeline_id']}", headers=first_headers).json()
    assert pipeline["steps"][0]["configuration"]["search_definition_id"] == definition_id

    db = session_factory()
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    product = Product(
        id="profile-product", category="gpu", brand="NVIDIA", family="RTX", model="RTX 3080",
        variant=None, display_name="NVIDIA RTX 3080", attributes={}, canonical_tier=1, created_at=now,
    )
    listings = [
        Listing(
            id=f"profile-listing-{index}", source="fixture", external_id=f"profile-listing-{index}", product_id=product.id,
            title=f"RTX 3080 {index}", price=2500, created_at=now,
        )
        for index in range(4)
    ]
    opportunities = [
        Opportunity(
            id=f"profile-opportunity-{index}", listing_id=listings[index].id, product_id=product.id,
            final_score=91 if index == 3 else 75, computed_at=now,
        )
        for index in range(4)
    ]
    competitor = Product(
        id="profile-competitor", category="cpu", brand="AMD", family="Ryzen", model="Ryzen 7",
        variant=None, display_name="AMD Ryzen 7", attributes={}, canonical_tier=1, created_at=now,
    )
    competitor_listing = Listing(
        id="profile-competitor-listing", source="fixture", external_id="profile-competitor-listing",
        product_id=competitor.id, title="Ryzen 7", price=2500, created_at=now,
    )
    competitor_opportunity = Opportunity(
        id="profile-competitor-opportunity", listing_id=competitor_listing.id, product_id=competitor.id,
        final_score=95, computed_at=now,
    )
    db.add_all([product, *listings, *opportunities, competitor, competitor_listing, competitor_opportunity])
    db.commit()
    db.close()


    for index in range(3):
        feedback = client.put(
            f"/api/v1/opportunities/profile-opportunity-{index}/feedback", headers=first_headers,
            json={"action": "saved", "note": "Boa margem", "reason": "Compatível com meu inventário"},
        )
        assert feedback.status_code == 200


        assert feedback.json()["feedback"] == "up"
        assert feedback.json()["metadata"]["reason"] == {
            "code": "USER_FEEDBACK", "message": "Compatível com meu inventário",
        }
    invalid = client.put(
        "/api/v1/opportunities/profile-opportunity-3/feedback", headers=first_headers,
        json={"feedback": "maybe"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "FEEDBACK_INVALID"
    isolated_opportunity = client.get("/api/v1/opportunities/profile-opportunity-3", headers=second_headers).json()
    assert isolated_opportunity["profile_feedback"] is None
    assert isolated_opportunity["preference_affinity"] == 0
    assert isolated_opportunity["personalized_score"] == isolated_opportunity["objective_score"]
    personal = client.get("/api/v1/opportunities/profile-opportunity-3", headers=first_headers).json()
    assert personal["objective_score"] == 91
    assert personal["final_score"] == 91
    assert personal["request_match_score"] == 100
    assert personal["preference_affinity"] == 10
    assert personal["personalized_score"] == 100
    assert personal["personalization_audit"]["code"] == "FEEDBACK_CONSENSUS"
    personalized_list = client.get("/api/v1/opportunities?page=1&page_size=10", headers=first_headers).json()
    assert personalized_list["items"][0]["id"] == "profile-opportunity-3"


def test_price_only_notebook_definition_uses_catalog_pipeline_scope(api_client):
    client, session_factory = api_client
    profile = _create_profile(client, "Catálogo notebook")
    headers = {"X-Profile-ID": profile["id"]}
    plan = compile_search_local("notebook até R$ 2.500").plan.model_dump(mode="json")
    definition = client.post(
        "/api/v1/search-definitions",
        headers=headers,
        json={"name": "Notebooks até R$ 2.500", "intent": plan["intent"], "plan": plan},
    )
    assert definition.status_code == 201, definition.text

    queued = client.post(
        "/api/v1/pipelines/run",
        headers=headers,
        json={"source_type": "fixture", "search_definition_id": definition.json()["id"]},
    )
    assert queued.status_code == 202, queued.text
    run = client.get(f"/api/v1/pipelines/{queued.json()['pipeline_id']}", headers=headers).json()
    scope = run["steps"][0]["configuration"]["scopes"][0]
    assert scope["catalog_match"] == "notebook-brasil-v1"
    assert scope["catalog_products"] == 342
    assert scope["query"] == "notebook"
    assert scope["max_price"] == 2500.0

    assert queued.json()["status"] == "pending"

    broad_queued = client.post(
        "/api/v1/pipelines/run",
        headers=headers,
        json={
            "source_type": "fixture",
            "search_definition_id": definition.json()["id"],
            "workload_mode": "high_volume",
            "duration_minutes": 30,
            "aggressiveness": "intensive",
        },
    )
    assert broad_queued.status_code == 202, broad_queued.text
    broad_run = client.get(f"/api/v1/pipelines/{broad_queued.json()['pipeline_id']}", headers=headers).json()
    broad_scope = broad_run["steps"][0]["configuration"]["scopes"][0]
    assert broad_scope["collection_mode"] is False
    assert broad_scope["scope_mode"] == "precise"
    assert broad_scope["catalog_match"] == "notebook-brasil-v1"

    db = session_factory()
    try:
        assert db.query(Product).filter(Product.id.like("catalog-notebook-%")).count() == 342
    finally:
        db.close()


def test_high_volume_definition_preserves_precise_scope(api_client):
    client, session_factory = api_client
    profile = _create_profile(client, "Coleta ampla por definição")
    headers = {"X-Profile-ID": profile["id"]}
    plan = compile_search_local("notebook até R$ 2.500").plan.model_dump(mode="json")
    definition = client.post(
        "/api/v1/search-definitions",
        headers=headers,
        json={"name": "Notebooks amplos", "intent": plan["intent"], "plan": plan},
    )
    assert definition.status_code == 201, definition.text

    queued = client.post(
        "/api/v1/pipelines/run",
        headers=headers,
        json={
            "source_type": "fixture",
            "search_definition_id": definition.json()["id"],
            "workload_mode": "high_volume",
            "duration_minutes": 30,
            "aggressiveness": "intensive",
        },
    )
    assert queued.status_code == 202, queued.text
    run = client.get(f"/api/v1/pipelines/{queued.json()['pipeline_id']}", headers=headers).json()
    scope = run["steps"][0]["configuration"]["scopes"][0]
    assert scope["collection_mode"] is False
    assert scope["scope_mode"] == "precise"
    assert scope["catalog_match"] == "notebook-brasil-v1"

    db = session_factory()
    try:
        assert db.query(Product).filter(Product.id.like("catalog-notebook-%")).count() == 342
    finally:
        db.close()


def test_saving_equivalent_sessions_reuses_one_search_definition(api_client):
    client, _ = api_client
    profile = _create_profile(client, "Escopos")
    headers = {"X-Profile-ID": profile["id"]}
    compiled_plan = compile_search_local("Notebook com tela IPS, Wi-Fi 5 GHz, até R$ 2.500").plan.model_dump(mode="json")
    intent = compiled_plan["intent"]
    # Simula o formato antigo salvo pelo modelo: os critérios estão certos,
    # mas as frases de recuperação não funcionam bem no campo de busca OLX.
    plan = {
        **compiled_plan,
        "primary_queries": ["notebook"],
        "fallback_queries": ["Notebook tela IPS, wifi 5ghz ate 2500"],
    }

    saved_definitions = []
    for raw_query in ("Notebook IPS Wi-Fi 5 GHz até R$ 2.500", "notebook com ips e wifi 5ghz até 2500"):
        draft_intent = {**intent, "raw_query": raw_query}
        session = client.post(
            "/api/v1/search-sessions",
            headers=headers,
            json={"draft": {"intent": draft_intent, "plan": {**plan, "intent": draft_intent}}},
        )
        assert session.status_code == 201, session.text
        saved = client.post(f"/api/v1/search-sessions/{session.json()['id']}/save", headers=headers)
        assert saved.status_code == 201, saved.text
        saved_definitions.append(saved.json()["definition"])

    assert saved_definitions[0]["id"] == saved_definitions[1]["id"]
    assert saved_definitions[1]["intent"]["raw_query"] == "notebook com ips e wifi 5ghz até 2500"
    assert saved_definitions[1]["plan"]["primary_queries"] == ["notebook IPS"]
    assert saved_definitions[1]["plan"]["fallback_queries"] == ["notebook"]
    definitions = client.get("/api/v1/search-definitions", headers=headers)
    assert definitions.status_code == 200
    assert len(definitions.json()["items"]) == 1


def test_profile_migration_backfills_legacy_personal_rows_and_is_idempotent(tmp_path):
    """Exercise the real Alembic chain, including SQLite batch constraints."""
    repository = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "legacy.sqlite"
    database_url = f"sqlite:///{database_path}"
    environment = {**os.environ, "DATABASE_URL": database_url}
    alembic = repository / ".venv" / "bin" / "alembic"

    subprocess.run(
        [str(alembic), "upgrade", "d7e9f3a8b4c1"],
        cwd=repository, env=environment, check=True, capture_output=True, text=True,
    )
    legacy_engine = create_engine(database_url)
    now = "2026-08-26 00:00:00"
    with legacy_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO user_preferences (preferences) VALUES (:preferences)"),
            {"preferences": '{"max_capital": 1234}'},
        )
        connection.execute(
            text(
                """
                INSERT INTO search_scopes
                    (id, name, marketplace, query, sort, "limit", enabled, created_at, updated_at)
                VALUES
                    ('legacy-scope', 'Legacy scope', 'fixture', 'RTX 3080', 'recent', 10, 1, :now, :now)
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO pipeline_runs
                    (id, type, status, started_at, duration_seconds, processed_count,
                     snapshots_created, products_normalized, opportunities_found, error_message, steps)
                VALUES
                    ('legacy-pipeline', 'fixture_ingest', 'completed', :now, 0, 0, 0, 0, 0, NULL, '[]')
                """
            ),
            {"now": now},
        )
    legacy_engine.dispose()

    subprocess.run(
        [str(alembic), "upgrade", "head"],
        cwd=repository, env=environment, check=True, capture_output=True, text=True,
    )
    # Repeat proves a resumed/automatic upgrade cannot duplicate the derived
    # SearchDefinition or create another migrated profile.
    subprocess.run(
        [str(alembic), "upgrade", "head"],
        cwd=repository, env=environment, check=True, capture_output=True, text=True,
    )
    check_engine = create_engine(database_url)
    with check_engine.connect() as connection:
        profile = connection.execute(text("SELECT id, name FROM profiles")).one()
        assert profile == ("profile-migrated", "Perfil migrado")
        assert connection.execute(text("SELECT profile_id FROM user_preferences")).scalar_one() == profile.id
        assert connection.execute(text("SELECT profile_id FROM search_scopes")).scalar_one() == profile.id
        assert connection.execute(text("SELECT profile_id FROM pipeline_runs")).scalar_one() == profile.id
        definition = connection.execute(
            text("SELECT profile_id, intent, plan FROM search_definitions WHERE id = 'search-def-legacy-legacy-scope'")
        ).one()
        assert definition.profile_id == profile.id
        assert "RTX 3080" in definition.intent
        assert "legacy-scope" in definition.plan
    check_engine.dispose()
