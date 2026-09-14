# GridScout — Autonomous Marketplace Intelligence & RAG Engine

GridScout is an enterprise-grade, local-first market intelligence platform designed to discover, normalize, and evaluate secondary market listings (specializing in consumer technology and hardware). By orchestrating an autonomous multi-stage agent pipeline, vector retrieval via **pgvector**, robust statistical modeling, and **Google Gemini** language models, GridScout extracts pricing inefficiencies while enforcing deterministic engineering guardrails.

---

## System Overview

Navigating secondary markets is notoriously noisy: listings lack canonical identifiers, contain ambiguous or misleading descriptions, and present fluctuating liquidity. GridScout solves this through a multi-tiered architecture:

1. **Autonomous Search & Scope Compilation**: Translates natural language user intents into formal, versioned search definitions and multi-query retrieval strategies.
2. **Retrieval-Augmented Generation (RAG) with pgvector**: Enriches candidate evaluations with deep domain knowledge (defect patterns, thermal concerns, hardware revision nuances) stored in PostgreSQL with pgvector embeddings.
3. **Deterministic Comparable & Valuation Engine**: Avoids LLM hallucination for mathematical tasks by computing non-parametric robust statistics (Median, Median Absolute Deviation [MAD], P10–P90 percentiles) across three comparable tiers.
4. **Autonomous Agent Investigation**: Deploys structured LLM tool-use (via Gemini 3.5 Flash Lite and 3.7 Flash) to analyze seller credibility, shipping/pickup evidence, and physical condition before assigning an actionable opportunity score.
5. **Operator Workbench UI**: A focused, responsive React 19 interface providing real-time pipeline monitoring, market analytics, interactive pipeline chat, and profile-scoped preferences.

---

## Architectural Breakdown

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      Operator Workbench UI                              │
│              React 19 · TypeScript · Vite · Tailwind CSS                │
│         Dashboard · Opportunities · Market · Pipelines · Searches       │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │ REST API (/api/v1)
┌────────────────────────────────────▼────────────────────────────────────┐
│                    FastAPI Core Service (apps/api)                      │
│     - Profile-Scoped State Isolation   - OpenAPI 3.1 Specification      │
│     - Natural Language Scope Compiler  - Real-time Pipeline Dispatch    │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │ Async Tasks & Shared State
┌────────────────────────────────────▼────────────────────────────────────┐
│                  Asynchronous Worker (apps/worker)                      │
│  ┌─────────────────────────┐  ┌──────────────────────────────────────┐  │
│  │ Pipeline Runner Engine  │  │ Deterministic Analytics              │  │
│  │ - Card Projection Gate  │  │ - 3-Tier Comparable Clustering       │  │
│  │ - Navigation Budgeting  │  │ - Robust Center & MAD Valuation      │  │
│  │ - Rate Limit Guards     │  │ - Market Heat & Velocity Scores      │  │
│  └───────────┬─────────────┘  └──────────────────────────────────────┘  │
│              │                                                          │
│  ┌───────────▼─────────────┐  ┌──────────────────────────────────────┐  │
│  │ AI & RAG Subsystem      │  │ Marketplace Ingestion                │  │
│  │ - Google Gemini API     │  │ - Playwright Ingestion Engine        │  │
│  │ - Vector Embeddings     │  │ - Static Fixture Source (Zero-Secret)│  │
│  │ - Tool Tracing Engine   │  │ - Read-Only Browser Isolation        │  │
│  └───────────┬─────────────┘  └──────────────────────────────────────┘  │
└──────────────┼──────────────────────────────────────────────────────────┘
               │ SQLAlchemy 2.0
┌──────────────▼──────────────────────────────────────────────────────────┐
│                   PostgreSQL 16 + pgvector Database                     │
│    - Relational Entities (Profiles, Listings, Snapshots, Pipelines)     │
│    - Vector Knowledge Base (Embeddings & Semantic Cosine Index)         │
│    - Versioned Database Migrations (Alembic)                            │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Key Technical Highlights

### 1. Vector Search & RAG Architecture (pgvector)
- **Hybrid Relational & Vector Storage**: Uses PostgreSQL 16 with the `pgvector` extension to store domain-specific diagnostic knowledge alongside relational marketplace listings and temporal price snapshots.
- **Semantic Retrieval Engine**: Embeds domain knowledge using Google `text-embedding-004` (or deterministic local embedding providers for offline testing). Queries match against indexed hardware knowledge using cosine distance metrics (`<=>`).
- **Context Injection**: Autonomous investigation agents query this vector store to cross-reference common hardware failure modes (e.g., thermal pad degradation on specific GPU architectures) against listing descriptions.

### 2. Google Gemini API & Multi-Model Gateway
- **Multi-Model Orchestration**: Leverages tiered model capabilities:
  - `gemini-3.5-flash-lite`: Fast, high-throughput extraction and canonical title normalization.
  - `gemini-3.7-flash`: Multi-step reasoning and autonomous investigation agent with tool execution.
  - `gemini-3.1-pro`: Deep analytical synthesis and pipeline query compilation.
- **Dual Authentication**: Seamlessly switches between direct **Gemini Developer API keys** (`GEMINI_API_KEY`) and Google Cloud **Vertex AI** (`VERTEX_PROJECT_ID` with Application Default Credentials).
- **Deterministic Local Fallbacks**: Fully operational in offline development mode (`MODEL_GATEWAY=local`) using deterministic mocks that simulate tool traces and structural analysis without external network calls.

### 3. Agent Guardrails & Navigation Budgeting
- **Strict Navigation Ceilings**: Crawling pipelines operate under strict navigation budget caps (e.g., max 30 navigation queries shared across alternatives) to eliminate infinite crawling and rate-limit triggers.
- **Two-Stage Card Gating (Price-First Gate)**: Before spending resource budget to fetch full listing details, candidate cards are filtered by preliminary pricing and eligibility thresholds.
- **Circuit Breakers & Leases**: Pipeline workers utilize cooperative lease acquisition, timeout reapers, and failure circuit breakers to guarantee graceful degradation.

### 4. Mathematical Rigor & Deterministic Normalization
- **3-Tier Comparable Matching**:
  - *Tier 1*: Identical canonical model and variant (e.g., RTX 3080 10GB).
  - *Tier 2*: Same generation and architectural class (+/-15% performance envelope).
  - *Tier 3*: Functional substitutes / adjacent performance cohorts.
- **Robust Price Statistics**: Replaces sensitive arithmetic means with Median, Median Absolute Deviation (MAD), and percentiles (P10, P25, P75, P90) to eliminate susceptibility to market anomalies or outlier listings.
- **Liquidity & Market Heat**: Combines listing velocity, disappearance rates, and duration to classify market segments into `COLD`, `NORMAL`, and `HOT` liquidity bands.

### 5. Benchmark & Evaluation Gates
- Built-in evaluation framework comparing pipeline outputs against curated ground-truth datasets:
  - Normalization Accuracy
  - Comparable Selection Precision@5
  - Valuation Error (Mean Absolute Error)
  - Retrieval Mean Reciprocal Rank (MRR)
  - Tool Invocation Success Rate

---

## Repository Structure

```text
gridscout/
├── apps/
│   ├── api/                 # FastAPI service: endpoints, schemas, authentication
│   ├── worker/              # Background worker: pipeline executor, scheduler
│   ├── browser_worker/      # Dedicated browser automation service
│   └── web/                 # React 19 frontend: TanStack Query, Tailwind CSS
├── packages/
│   ├── ai/                  # Model gateway (Gemini API & Vertex AI adapters)
│   ├── catalog/             # Hardware taxonomy, product catalogs, SKU resolution
│   ├── classification/      # Domain classification & scope guardrails
│   ├── core/                # Database models, schemas, audit trails, configurations
│   ├── market/              # Robust statistical valuation, liquidity & heat engine
│   ├── marketplace/         # Source adapters (Playwright ingestion & fixture mocks)
│   ├── pipeline/            # End-to-end execution, card gate, discovery analysis
│   ├── retrieval/           # pgvector retrieval & semantic search index
│   └── search/              # Natural language compiler, query planning, matching
├── fixtures/                # Deterministic datasets (knowledge, evals, marketplace)
├── migrations/              # Alembic schema versioning scripts
├── scripts/                 # Operational scripts (seed, test, dev, reset, live_logs)
├── compose.yaml             # Multi-container Docker Compose configuration
└── tests/                   # Test suite (unit, integration, browser, e2e)
```

---

## Getting Started

### Prerequisites
- **Docker** & **Docker Compose** (recommended)
- Alternatively: **Python 3.12+**, **Node.js 20+**, and **PostgreSQL 16 with pgvector**

### 1. Environment Configuration
Copy the provided `.env.example` template:
```bash
cp .env.example .env
```
The application defaults to `APP_MODE=local`, enabling full local execution using synthetic data fixtures and deterministic mock models without requiring external API keys.

To enable live Google Gemini integration:
```env
APP_MODE=live
MODEL_GATEWAY=gemini
GENAI_AUTH_MODE=gemini_api
GEMINI_API_KEY=your_actual_gemini_api_key_here
```

### 2. Running with Docker Compose (Recommended)
Launch the complete stack (PostgreSQL with pgvector, API, worker, and frontend):
```bash
docker compose up --build -d
```
- **Web UI**: [http://localhost:3000](http://localhost:3000)
- **API Documentation (Swagger/OpenAPI)**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **API Health Check**: [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health)

Migrations and catalog initialization execute automatically on startup via `db-init`.

### 3. Local Development (Without Docker)

#### Backend Setup
```bash
# 1. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Apply database migrations
alembic upgrade head

# 4. Seed initial catalog and benchmark data
python -m packages.core.seed

# 5. Start API and Worker
uvicorn apps.api.main:app --reload --port 8000
python -m apps.worker.main
```

#### Frontend Setup
```bash
cd apps/web
npm install
npm run dev
```
The Vite development server will be available at `http://localhost:5173` (proxied to API at port 8000).

---

## Testing & Quality Verification

GridScout enforces comprehensive automated quality gates across backend logic, vector search, browser extraction, and frontend interfaces.

### Run All Quality Gates
Execute the comprehensive test pipeline:
```bash
./scripts/test.sh
```

### Individual Test Suites

#### Backend Test Suite (pytest)
Runs unit, integration, and end-to-end tests:
```bash
.venv/bin/pytest tests/ -v
```

#### Playwright Browser Extraction Tests
Tests headless HTML extraction using local fixtures without external network traffic:
```bash
.venv/bin/pytest tests/browser/ -v
```

#### Frontend Testing & Production Build
Executes Vitest component tests, TypeScript verification, and production bundling:
```bash
cd apps/web
npm test
npm run build
```

---

## Security & Privacy Guardrails

- **Zero Secret Leakage**: All sensitive configurations are managed via `.gitignore`-excluded environment files. No live API credentials or tokens are committed.
- **Read-Only Ingestion**: Ingestion paths are strictly read-only; no marketplace mutations, automatic purchases, messaging, or form submission capabilities exist.
- **Profile Isolation**: Data mutations, pipeline sessions, and search histories are partitioned by profile context (`X-Profile-ID`), preventing cross-tenant information disclosure.
- **Audit Logging**: Every pipeline run, scoring modification, and evaluation run is persisted with an immutable audit trail.

---

## License

MIT License. See [LICENSE](LICENSE) for details.
