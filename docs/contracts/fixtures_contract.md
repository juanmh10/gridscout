# Synthetic Fixtures & Ground Truth Contract (v1)

This document establishes the expectations for synthetic fixtures, ground truth datasets, and local browser pages across the Market Radar MVP.

---

## 1. Marketplace Fixture Structure

Fixture files live under `fixtures/marketplace/`:
- `fixtures/marketplace/listings_v1.json`: Synthetic listings with temporal history.
- `fixtures/marketplace/snapshots_v1.json`: Observed snapshots over a 30-day timeline.
- `fixtures/marketplace/html/`: Static HTML marketplace search and detail pages for Playwright fixture tests.

### 1.1 Dataset Volumes & Categories

- Total listings: ~200 deterministic listings.
- Observation window: 30 days.
- Core categories:
  - `gpu` (Primary focus: RTX 3060, 3070, 3080 10GB, 3080 12GB, 3080 Ti, RX 6700 XT, RX 6800 XT)
  - `notebook` (Primary focus: MacBook Air M1, MacBook Pro M1 Pro, Dell G15 RTX 3060, Lenovo Legion 5)
  - `cpu` (Ryzen 5 5600X, Ryzen 7 5800X3D, Core i5-12400F, Core i7-13700K)
  - `ram` (DDR4 16GB 3200MHz, DDR5 32GB 6000MHz)
  - `ssd` (NVMe 1TB Gen4, NVMe 2TB Gen4)
  - `motherboard` (B550, B650, Z690)

### 1.2 Required Fixture Edge Cases

1. **Exact vs Variant Matches**:
   - `RTX 3080 10GB` vs `RTX 3080 12GB` vs `RTX 3080 Ti` (Tier 1 exact vs Tier 2 adjacent).
   - `MacBook Air M1 8GB/256GB` vs `16GB/512GB` (different canonical identities and clearing values).
2. **Temporal Dynamics**:
   - Fast disappearance (< 3 days): underpriced listings.
   - Normal turnover (7–14 days).
   - Stale inventory (> 25 days): overpriced listings.
   - Price drops across snapshots (e.g. R$ 2.600 -> R$ 2.400 -> R$ 2.100).
3. **Outliers & Suspicious Listings**:
   - Impossible price (e.g. RTX 3080 for R$ 450 with "call whatsapp" in description) -> flagged with high risk penalty.
   - For parts / defective items (e.g. "com artefatos na tela", "para retirar peças") -> condition class `for_parts`.
4. **Ambiguous Titles**:
   - "Placa de video gamer 3080 top" -> normalized to `prod-gpu-rtx3080` with attribute extraction.

---

## 2. Product Knowledge Fixtures

Fixture files live under `fixtures/product_knowledge/`:
- `fixtures/product_knowledge/knowledge_v1.json`: Semistructured documents for vector indexing.

Each record includes:
- `id`: Unique identifier (e.g., `pk-gpu-rtx3080-01`)
- `product_id`: Canonical product ID
- `category`: Category string
- `title`: Short title
- `body`: Diagnostic / hardware note
- `metadata`: JSON attributes (critical inspection points, defects, compatibility)

---

## 3. Evaluation & Benchmark Fixtures

Fixture files live under `fixtures/evals/`:
- `fixtures/evals/ground_truth_v1.json`:
  - `normalization_cases`: Raw titles mapped to expected canonical product IDs.
  - `comparable_cases`: Target listing ID with expected Tier 1 and Tier 2 comparable IDs.
  - `market_stats_cases`: Product ID with expected median, clearing value, and heat band within defined tolerances.
  - `retrieval_cases`: Query strings with relevant document IDs for Precision@K and MRR.
  - `opportunity_cases`: Expected top 10 opportunity IDs and expected score ranges.

---

## 4. Playwright Browser Fixtures

Fixture files live under `fixtures/marketplace/html/`:
- `index.html` / `search.html`: Mock marketplace search results page containing items, prices, locations, timestamps.
- `items/101.html`, `items/102.html`, etc.: Mock listing detail pages with title, full description, seller information, attributes.
- Local static file server or test server to host these pages during Playwright test runs.
