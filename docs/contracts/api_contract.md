# Frozen API Contract — Market Radar MVP (v1)

This document defines the frozen REST API contract for the Market Radar MVP.
Both backend services and frontend applications adhere to this contract.

Base URL prefix: `/api/v1`

---

## 1. General Conventions

### 1.1 Error Envelope

HTTP 4xx and 5xx responses must return:

```json
{
  "error": {
    "code": "RESOURCE_NOT_FOUND",
    "message": "Detailed human-readable error explanation.",
    "details": {}
  }
}
```

Standard Error Codes:
- `VALIDATION_ERROR`: Malformed input or invalid query parameter.
- `RESOURCE_NOT_FOUND`: Specified ID does not exist.
- `EXTERNAL_PROVIDER_DISABLED`: Attempted action requires external provider that is disabled in MVP mode.
- `PIPELINE_ERROR`: Pipeline execution encountered a failure.
- `INTERNAL_SERVER_ERROR`: Unhandled exception.

### 1.2 Pagination Envelope

All paginated collection endpoints return:

```json
{
  "items": [],
  "total": 150,
  "page": 1,
  "page_size": 20,
  "pages": 8
}
```

Standard query parameters for pagination:
- `page` (integer, default `1`, minimum `1`)
- `page_size` (integer, default `20`, minimum `1`, maximum `100`)

### 1.3 Profile Context

Profile-owned endpoints require the `X-Profile-ID` request header. Missing
context returns `PROFILE_REQUIRED`, an unknown profile returns
`PROFILE_NOT_FOUND`, and a missing or differently-owned resource always
returns `RESOURCE_NOT_FOUND` so ownership is not disclosed.

Unscoped profile bootstrap endpoints are `GET /api/v1/profiles`,
`POST /api/v1/profiles`, and `PATCH /api/v1/profiles/{id}`. Profile-owned
resources include settings, search scopes/definitions/sessions, pipelines,
pipeline chat, dashboard pipeline history, and opportunity feedback.

Search construction uses versioned `SearchIntentV1` and `SearchPlanV1` JSON
through `/api/v1/search-definitions` and `/api/v1/search-sessions`. Session
messages accept `client_request_id` for idempotency. Sessions may save a
definition, but do not execute pipelines; pipeline execution is initiated only
through `/api/v1/pipelines/run`. Opportunity feedback is upserted or removed
through `PUT/DELETE /api/v1/opportunities/{id}/feedback`.

---

## 2. Enums and Shared Types

### `AppMode`
`"local"` | `"live"`

### `ListingStatus`
`"active"` | `"disappeared"` | `"stale"` | `"removed"`

### `ConditionClass`
`"new"` | `"like_new"` | `"good"` | `"fair"` | `"for_parts"`

### `DeliveryStatus`
`"AVAILABLE"` | `"UNAVAILABLE"` | `"UNKNOWN"`

Delivery is classified only from evidence visible on the original listing page.
`UNKNOWN` means that the page did not provide enough evidence.

### `MarketHeatBand`
`"COLD"` | `"NORMAL"` | `"HOT"`
- `0.0 - 34.0`: `"COLD"`
- `35.0 - 69.0`: `"NORMAL"`
- `70.0 - 100.0`: `"HOT"`

### `PipelineStatus`
`"pending"` | `"running"` | `"completed"` | `"failed"`

### `InvestigationStatus`
`"uninvestigated"` | `"in_progress"` | `"completed"` | `"failed"`

### `ComparableTier`
- `1`: Same canonical product and variant.
- `2`: Same class/generation with bounded spec differences.
- `3`: Functional substitute / adjacent product.

---

## 3. Endpoints Specification

### 3.1 Health & System Status

#### `GET /api/v1/health`
Response (200 OK):
```json
{
  "status": "ok",
  "timestamp": "2026-08-23T16:00:00Z",
  "database": "connected"
}
```

#### `GET /api/v1/status`
Response (200 OK):
```json
{
  "app_mode": "local",
  "synthetic_data": true,
  "fixture_version": "v1.0.0",
  "external_providers": {
    "olx": "disabled",
    "vertex_ai": "disabled",
    "vertex_search": "disabled",
    "gemini_embeddings": "disabled"
  },
  "active_adapters": {
    "marketplace": "FixtureMarketplaceSource",
    "model": "DeterministicLocalModelGateway",
    "embedding": "LocalHashEmbeddingProvider",
    "retriever": "PgVectorRetriever"
  }
}
```

---

### 3.2 Dashboard

#### `GET /api/v1/dashboard`
Response (200 OK):
```json
{
  "active_listing_count": 182,
  "opportunity_count": 14,
  "pipeline_runs_count": 5,
  "latest_pipeline_status": "completed",
  "top_market_heat": [
    {
      "product_id": "prod-gpu-rtx3080",
      "product_name": "NVIDIA GeForce RTX 3080 10GB",
      "category": "gpu",
      "heat_score": 84.5,
      "heat_band": "HOT",
      "sample_size": 28,
      "median_price": 2850.0
    }
  ],
  "top_opportunities": [
    {
      "id": "opp-001",
      "listing_id": "list-101",
      "product_id": "prod-gpu-rtx3080",
      "product_name": "NVIDIA GeForce RTX 3080 10GB",
      "category": "gpu",
      "asking_price": 2100.0,
      "estimated_clearing_value": 2750.0,
      "fast_sale_value": 2400.0,
      "price_edge": 0.236,
      "final_score": 88.4,
      "market_heat": 84.5,
      "heat_band": "HOT",
      "confidence": 0.92,
      "condition": "like_new",
      "location": "São Paulo, SP",
      "first_seen": "2026-08-20T10:00:00Z"
    }
  ],
  "recent_pipelines": [
    {
      "id": "pipe-001",
      "type": "synthetic_full_ingest",
      "status": "completed",
      "started_at": "2026-08-23T15:30:00Z",
      "finished_at": "2026-08-23T15:30:12Z",
      "processed_count": 240,
      "opportunities_found": 14
    }
  ],
  "market_trend_summary": {
    "avg_price_change_7d": -0.024,
    "hot_categories": ["gpu", "notebook"],
    "cold_categories": ["motherboard"]
  }
}
```

---

### 3.3 Listings

#### `GET /api/v1/listings`
Query Parameters:
- `page` (int, default 1)
- `page_size` (int, default 20)
- `category` (string, optional)
- `product_id` (string, optional)
- `status` (ListingStatus, optional)
- `min_price` (float, optional)
- `max_price` (float, optional)
- `search` (string, optional)
- `source` (string, optional)

Response (200 OK):
```json
{
  "items": [
    {
      "id": "list-101",
      "source": "fixture",
      "external_id": "synth-gpu-001",
      "product_id": "prod-gpu-rtx3080",
      "product_name": "NVIDIA GeForce RTX 3080 10GB",
      "category": "gpu",
      "title": "RTX 3080 EVGA FTW3 Ultra 10GB impecável",
      "price": 2100.0,
      "location_state": "SP",
      "location_city": "São Paulo",
      "seller_name": "TechTrader_SP",
      "condition": "like_new",
      "delivery_status": "AVAILABLE",
      "delivery_evidence": ["Envio disponível para todo o Brasil"],
      "seller_verification": "VERIFIED",
      "seller_signal_level": "HIGH",
      "seller_evidence": ["Usuário verificado"],
      "analysis_summary": "RTX 3080 EVGA FTW3 Ultra 10GB impecável. Placa usada apenas para jogos...",
      "analysis_status": "completed",
      "source_url": "http://fixture.local/items/101",
      "first_seen": "2026-08-20T10:00:00Z",
      "last_seen": "2026-08-23T14:00:00Z",
      "status": "active",
      "normalization_confidence": 0.95,
      "snapshots_count": 3
    }
  ],
  "total": 182,
  "page": 1,
  "page_size": 20,
  "pages": 10
}
```

#### `GET /api/v1/listings/{id}`
Response (200 OK):
```json
{
  "id": "list-101",
  "source": "fixture",
  "external_id": "synth-gpu-001",
  "product_id": "prod-gpu-rtx3080",
  "product_name": "NVIDIA GeForce RTX 3080 10GB",
  "category": "gpu",
  "title": "RTX 3080 EVGA FTW3 Ultra 10GB impecável",
  "description": "Placa usada apenas para jogos, nunca minerada. Acompanha caixa original e nota fiscal.",
  "price": 2100.0,
  "location_state": "SP",
  "location_city": "São Paulo",
  "seller_name": "TechTrader_SP",
  "seller_rating": 4.9,
  "condition": "like_new",
  "delivery_status": "AVAILABLE",
  "delivery_evidence": ["Envio disponível para todo o Brasil"],
  "seller_verification": "VERIFIED",
  "seller_signal_level": "HIGH",
  "seller_evidence": ["Usuário verificado"],
  "analysis_summary": "RTX 3080 EVGA FTW3 Ultra 10GB impecável. Placa usada apenas para jogos...",
  "analysis_status": "completed",
  "attributes": {
    "vram": "10GB",
    "cooler": "Triple Fan",
    "box_included": true
  },
  "source_url": "http://fixture.local/items/101",
  "first_seen": "2026-08-20T10:00:00Z",
  "last_seen": "2026-08-23T14:00:00Z",
  "status": "active",
  "normalization_confidence": 0.95,
  "snapshots": [
    {
      "id": "snap-1",
      "listing_id": "list-101",
      "observed_at": "2026-08-20T10:00:00Z",
      "price": 2300.0,
      "status": "active"
    },
    {
      "id": "snap-2",
      "listing_id": "list-101",
      "observed_at": "2026-08-22T08:00:00Z",
      "price": 2100.0,
      "status": "active"
    }
  ]
}
```

---

### 3.4 Products and Market Analysis

#### `GET /api/v1/products`
Query Parameters:
- `page` (int, default 1)
- `page_size` (int, default 20)
- `category` (string, optional)
- `search` (string, optional)

Response (200 OK):
```json
{
  "items": [
    {
      "id": "prod-gpu-rtx3080",
      "category": "gpu",
      "brand": "NVIDIA",
      "family": "GeForce RTX 30 Series",
      "model": "RTX 3080",
      "variant": "10GB",
      "display_name": "NVIDIA GeForce RTX 3080 10GB",
      "attributes": {
        "memory_size_gb": 10,
        "memory_type": "GDDR6X",
        "tdp_watts": 320
      },
      "active_listings_count": 18,
      "market_median_price": 2850.0
    }
  ],
  "total": 12,
  "page": 1,
  "page_size": 20,
  "pages": 1
}
```

#### `GET /api/v1/products/{id}`
Response (200 OK): Canonical Product details.

#### `GET /api/v1/products/{id}/market`
Response (200 OK):
```json
{
  "product_id": "prod-gpu-rtx3080",
  "product_name": "NVIDIA GeForce RTX 3080 10GB",
  "category": "gpu",
  "window_days": 30,
  "sample_size": 34,
  "active_count": 18,
  "disappeared_count": 16,
  "asking_median": 2850.0,
  "estimated_clearing_value": 2700.0,
  "fast_sale_value": 2350.0,
  "robust_center": 2720.0,
  "p10": 2200.0,
  "p25": 2550.0,
  "median": 2850.0,
  "p75": 3100.0,
  "p90": 3400.0,
  "mad": 280.0,
  "market_heat": 84.5,
  "heat_band": "HOT",
  "confidence": 0.94,
  "listing_velocity": 1.13,
  "disappearance_velocity": 0.94,
  "median_visible_duration_days": 4.2,
  "price_trend_30d": -0.035,
  "calculated_at": "2026-08-23T15:30:00Z"
}
```

#### `GET /api/v1/products/{id}/comparables`
Query Parameters:
- `tier` (int, optional: 1, 2, or 3)

Response (200 OK):
```json
{
  "product_id": "prod-gpu-rtx3080",
  "comparables": [
    {
      "listing_id": "list-101",
      "title": "RTX 3080 EVGA FTW3 Ultra 10GB",
      "price": 2100.0,
      "condition": "like_new",
      "status": "active",
      "tier": 1,
      "similarity_weight": 1.0,
      "observed_date": "2026-08-22T08:00:00Z",
      "tier_reason": "Exact canonical product and 10GB variant match"
    },
    {
      "listing_id": "list-109",
      "title": "RTX 3080 Ti ASUS TUF 12GB",
      "price": 3100.0,
      "condition": "good",
      "status": "disappeared",
      "tier": 2,
      "similarity_weight": 0.85,
      "observed_date": "2026-08-19T14:00:00Z",
      "tier_reason": "Same generation high-end cohort (+15% performance bound)"
    }
  ]
}
```

#### `GET /api/v1/products/{id}/history`
Response (200 OK):
```json
{
  "product_id": "prod-gpu-rtx3080",
  "history": [
    {
      "date": "2026-07-25",
      "asking_median": 2950.0,
      "clearing_estimate": 2800.0,
      "active_count": 22,
      "disappeared_count": 4
    },
    {
      "date": "2026-08-01",
      "asking_median": 2900.0,
      "clearing_estimate": 2750.0,
      "active_count": 20,
      "disappeared_count": 6
    }
  ]
}
```

---

### 3.5 Opportunities

#### `GET /api/v1/opportunities`
Query Parameters:
- `page` (int, default 1)
- `page_size` (int, default 20)
- `min_score` (float, optional, e.g. 60.0)
- `category` (string, optional)
- `heat_band` (MarketHeatBand, optional)
- `delivery_status` (DeliveryStatus, optional)
- `seller_signal_level` (`HIGH`, `MEDIUM`, `LOW`, `UNKNOWN`, optional)
- `sort_by` (string, default `score_desc`, options: `score_desc`, `edge_desc`, `price_asc`, `freshness_desc`)

Response (200 OK):
```json
{
  "items": [
    {
      "id": "opp-001",
      "listing_id": "list-101",
      "product_id": "prod-gpu-rtx3080",
      "product_name": "NVIDIA GeForce RTX 3080 10GB",
      "category": "gpu",
      "title": "RTX 3080 EVGA FTW3 Ultra 10GB impecável",
      "asking_price": 2100.0,
      "estimated_clearing_value": 2700.0,
      "fast_sale_value": 2350.0,
      "price_edge": 0.222,
      "final_score": 88.4,
      "market_heat": 84.5,
      "heat_band": "HOT",
      "confidence": 0.94,
      "condition": "like_new",
      "location": "São Paulo, SP",
      "delivery_status": "AVAILABLE",
      "delivery_evidence": ["Envio disponível para todo o Brasil"],
      "seller_verification": "VERIFIED",
      "seller_signal_level": "HIGH",
      "seller_evidence": ["Usuário verificado"],
      "analysis_summary": "RTX 3080 EVGA FTW3 Ultra 10GB impecável. Placa usada apenas para jogos...",
      "analysis_status": "completed",
      "first_seen": "2026-08-20T10:00:00Z",
      "investigation_status": "completed"
    }
  ],
  "total": 14,
  "page": 1,
  "page_size": 20,
  "pages": 1
}
```

#### `GET /api/v1/opportunities/{id}`
Response (200 OK):
```json
{
  "id": "opp-001",
  "listing_id": "list-101",
  "product_id": "prod-gpu-rtx3080",
  "product_name": "NVIDIA GeForce RTX 3080 10GB",
  "category": "gpu",
  "title": "RTX 3080 EVGA FTW3 Ultra 10GB impecável",
  "description": "Placa usada apenas para jogos, nunca minerada. Acompanha caixa original e nota fiscal.",
  "asking_price": 2100.0,
  "estimated_clearing_value": 2700.0,
  "fast_sale_value": 2350.0,
  "price_edge": 0.222,
  "final_score": 88.4,
  "market_heat": 84.5,
  "heat_band": "HOT",
  "confidence": 0.94,
  "condition": "like_new",
  "location": "São Paulo, SP",
  "seller_name": "TechTrader_SP",
  "delivery_status": "AVAILABLE",
  "delivery_evidence": ["Envio disponível para todo o Brasil"],
  "seller_verification": "VERIFIED",
  "seller_signal_level": "HIGH",
  "seller_evidence": ["Usuário verificado"],
  "analysis_summary": "RTX 3080 EVGA FTW3 Ultra 10GB impecável. Placa usada apenas para jogos...",
  "analysis_status": "completed",
  "source_url": "http://fixture.local/items/101",
  "first_seen": "2026-08-20T10:00:00Z",
  "last_seen": "2026-08-23T14:00:00Z",
  "score_breakdown": {
    "price_edge_component": 35.5,
    "liquidity_component": 21.0,
    "condition_component": 15.0,
    "personal_fit_component": 15.0,
    "freshness_component": 7.5,
    "convenience_component": 4.4,
    "risk_penalty": 10.0
  },
  "explanation": "Price is 22.2% below estimated clearing value with strong liquidity in São Paulo.",
  "investigation": {
    "status": "completed",
    "verdict": "ATTRACTIVE_OPPORTUNITY",
    "confidence": 0.92,
    "key_evidence": [
      "Asking price R$ 2.100 is 22.2% below 30-day clearing median of R$ 2.700",
      "Seller provides original box and receipt (NF)",
      "High liquidity market with 4.2 days median visible duration"
    ],
    "risks": [
      "Verify thermal paste and pad condition during in-person pickup",
      "Confirm serial number matches invoice"
    ],
    "market_summary": "High demand cohort. Disappearance velocity indicates fast turnover under R$ 2.400.",
    "suggested_next_checks": [
      "Ask seller for 3DMark TimeSpy benchmark video",
      "Arrange in-person test in São Paulo"
    ],
    "tool_trace": [
      {
        "tool_name": "get_market_snapshot",
        "input": { "product_id": "prod-gpu-rtx3080" },
        "output": { "median": 2850.0, "clearing_estimate": 2700.0, "sample_size": 34 }
      },
      {
        "tool_name": "retrieve_product_knowledge",
        "input": { "query": "RTX 3080 EVGA thermal pad issues", "category": "gpu" },
        "output": { "matches_found": 2, "top_snippet": "EVGA FTW3 VRAM thermal pads require check on high loads." }
      }
    ]
  }
}
```

---

### 3.6 Pipelines

#### `GET /api/v1/pipelines`
Query Parameters:
- `page` (int, default 1)
- `page_size` (int, default 20)

Response (200 OK):
```json
{
  "items": [
    {
      "id": "pipe-001",
      "type": "synthetic_full_ingest",
      "status": "completed",
      "started_at": "2026-08-23T15:30:00Z",
      "finished_at": "2026-08-23T15:30:12Z",
      "duration_seconds": 12.4,
      "processed_count": 240,
      "snapshots_created": 320,
      "products_normalized": 240,
      "opportunities_found": 14,
      "error_message": null
    }
  ],
  "total": 5,
  "page": 1,
  "page_size": 20,
  "pages": 1
}
```

#### `GET /api/v1/pipelines/{id}`
Response (200 OK):
```json
{
  "id": "pipe-001",
  "type": "synthetic_full_ingest",
  "status": "completed",
  "started_at": "2026-08-23T15:30:00Z",
  "finished_at": "2026-08-23T15:30:12Z",
  "duration_seconds": 12.4,
  "processed_count": 240,
  "snapshots_created": 320,
  "products_normalized": 240,
  "opportunities_found": 14,
  "error_message": null,
  "steps": [
    {
      "name": "fixture_ingest",
      "status": "completed",
      "duration_seconds": 1.2,
      "items_in": 240,
      "items_out": 240
    },
    {
      "name": "product_normalization",
      "status": "completed",
      "duration_seconds": 2.8,
      "items_in": 240,
      "items_out": 240
    },
    {
      "name": "market_statistics",
      "status": "completed",
      "duration_seconds": 3.1,
      "items_in": 12,
      "items_out": 12
    },
    {
      "name": "opportunity_scoring",
      "status": "completed",
      "duration_seconds": 2.3,
      "items_in": 240,
      "items_out": 14
    },
    {
      "name": "agent_investigation",
      "status": "completed",
      "duration_seconds": 3.0,
      "items_in": 14,
      "items_out": 14
    }
  ]
}
```

#### `POST /api/v1/pipelines/run`
Request Body:
```json
{
  "source_type": "fixture",
  "search_definition_id": "search-def-optional",
  "fixture_version": "v1.0.0",
  "query": "",
  "limit": 5,
  "model_mode": "auto",
  "investigate_limit": 2,
  "workload_mode": "high_volume",
  "duration_minutes": 30,
  "aggressiveness": "balanced"
}
```
`search_definition_id` is optional and profile-owned. When supplied it is the
sole scope input, is converted to immutable search-plan snapshots, and cannot
be combined with `scope_ids` or `ad_hoc_scope`.

For `workload_mode: "high_volume"`, `duration_minutes` accepts 5–180 and
`aggressiveness` accepts `conservative`, `balanced` or `intensive`. The server
derives the navigation cap, discovery/detail allocation and exact wall-clock
deadline; callers cannot override those safeguards.
High-volume requests also accept `detail_access_mode` (`disabled` by default,
or `auto_threshold`) and `enrichment_threshold` (0–100, default 70). Disabled
mode performs discovery and card analysis without opening listing URLs.
Response (202 Accepted):
```json
{
  "pipeline_id": "pipe-002",
  "status": "pending",
  "message": "Pipeline persisted and queued for the worker."
}
```

#### `POST /api/v1/pipelines/high-volume/preflight`

Returns a non-reserving forecast for the same scope that will be sent to
`/pipelines/run`. It accepts the high-volume fields above plus exactly one
scope input (`search_definition_id`, `scope_ids`, `ad_hoc_scope` or `query`).

```json
{
  "mode": "high_volume",
  "duration_minutes": 30,
  "aggressiveness": "intensive",
  "pace_seconds": 65,
  "navigation_cap": 27,
  "estimated_discoveries": {"min": 600, "max": 900},
  "estimated_details_max": 7,
  "can_start": true,
  "state": "ready"
}
```

Discovery estimates use completed runs from the same profile and scope when
available, otherwise a conservative per-page range. OLX capacity can change
between this forecast and enqueue; the enqueue endpoint repeats the guard.

#### `POST /api/v1/pipelines/{id}/enrichment`

Queues detail visits from persisted preliminary signals without repeating
search pages. The body accepts `threshold`, `max_items`, optional `min_price`,
`max_price`, `category`, and `location`; OLX capacity is checked first.

`GET /api/v1/opportunities` accepts `stage=all|preliminary|confirmed`. Items
expose `stage` and `full_flow_completed`; preliminary items use
`preliminary_score` and do not claim confirmed margin, seller, or delivery.

---

### 3.7 Retrieval (Product Knowledge)

#### `POST /api/v1/retrieval/search`
Request Body:
```json
{
  "query": "RTX 3080 thermal pad defects and power connector issues",
  "category": "gpu",
  "limit": 5
}
```
Response (200 OK):
```json
{
  "query": "RTX 3080 thermal pad defects and power connector issues",
  "results": [
    {
      "id": "pk-gpu-rtx3080-01",
      "product_id": "prod-gpu-rtx3080",
      "category": "gpu",
      "title": "NVIDIA RTX 3080 Thermal VRAM Inspection",
      "body": "Early batches of GDDR6X cards run hot. Ensure VRAM thermal pads have not deteriorated or leaked silicon oil.",
      "score": 0.884,
      "metadata": {
        "generation": "Ampere",
        "critical_check": "VRAM temperature under load"
      }
    }
  ]
}
```

---

### 3.8 Benchmarks and Evaluation

#### `GET /api/v1/benchmarks`
Response (200 OK):
```json
{
  "items": [
    {
      "id": "eval-run-001",
      "dataset_version": "v1.0.0",
      "executed_at": "2026-08-23T15:45:00Z",
      "configuration": "deterministic_local_v1",
      "metrics": {
        "normalization_accuracy": 0.962,
        "comparable_precision_at_5": 0.940,
        "price_error_mae": 42.50,
        "retrieval_mrr": 0.915,
        "opportunity_precision_at_10": 0.900,
        "tool_success_rate": 1.0,
        "pipeline_duration_seconds": 11.8
      },
      "failures_count": 2,
      "status": "completed"
    }
  ],
  "total": 3,
  "page": 1,
  "page_size": 20,
  "pages": 1
}
```

#### `GET /api/v1/benchmarks/{id}`
Response (200 OK): Detailed benchmark run including granular evaluation cases and failure list.

#### `POST /api/v1/benchmarks/run`
Request Body:
```json
{
  "dataset_version": "v1.0.0"
}
```
Response (202 Accepted):
```json
{
  "benchmark_id": "eval-run-002",
  "status": "pending",
  "message": "Benchmark suite started."
}
```

---

### 3.9 Marketplace Session (opt-in live profile)

#### `POST /api/v1/marketplace/auth/start?marketplace=olx`

Request body:
```json
{ "email": "buyer@example.com" }
```

The live browser-worker opens a visible WSLg browser on demand, navigates to the OLX login page and fills only this
email. If the dedicated browser was closed, it is reopened automatically. The API never stores the email or a
password. The response contains a `code_required` stage when OLX requests the one-time code.

#### `POST /api/v1/marketplace/auth/verify-code?marketplace=olx`

Request body:
```json
{ "code": "123456" }
```

The code is forwarded only to the active browser session and is not persisted or logged. The final Playwright
storage state is saved only after the browser leaves the login page.

#### `GET /api/v1/marketplace/auth/status`

Returns the availability of the shared Playwright storage state, cookie count, browser-worker readiness, login stage,
optional error code and the fixed read-only scope: `["search", "listing_detail"]`. Browser and login failures use
FastAPI's standard error envelope with a structured detail object:

```json
{
  "detail": {
    "code": "login_window_closed",
    "message": "A janela de login foi fechada. Inicie o login novamente."
  }
}
```

#### `POST /api/v1/marketplace/auth/logout`

Removes the local storage state. No marketplace write operation is exposed.

### 3.10 Settings and User Preferences

#### `GET /api/v1/settings`
Response (200 OK):
```json
{
  "app_mode": "local",
  "synthetic_data": true,
  "fixture_version": "v1.0.0",
  "external_providers": {
    "olx": "disabled",
    "vertex_ai": "disabled",
    "vertex_search": "disabled",
    "gemini_embeddings": "disabled"
  },
  "preference_profile": {
    "target_categories": ["gpu", "notebook", "cpu"],
    "category_expertise": {
      "gpu": 1.0,
      "notebook": 0.85,
      "cpu": 0.9,
      "ram": 0.7,
      "ssd": 0.7
    },
    "max_capital": 5000.0,
    "min_desired_edge": 0.10,
    "risk_tolerance": 0.45,
    "preferred_location": "SP"
  }
}
```

#### `PUT /api/v1/settings`
Request Body:
```json
{
  "preference_profile": {
    "target_categories": ["gpu", "notebook", "cpu"],
    "category_expertise": {
      "gpu": 1.0,
      "notebook": 0.90,
      "cpu": 0.9
    },
    "max_capital": 6000.0,
    "min_desired_edge": 0.12,
    "risk_tolerance": 0.40,
    "preferred_location": "SP"
  }
}
```
Response (200 OK): Updated settings object.
