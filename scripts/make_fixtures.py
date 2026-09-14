import json
import os

os.makedirs("fixtures/marketplace/html/items", exist_ok=True)
os.makedirs("fixtures/product_knowledge", exist_ok=True)
os.makedirs("fixtures/evals", exist_ok=True)

# Product Knowledge
pk = [
    {
        "id": "pk-gpu-rtx3080-01",
        "product_id": "prod-gpu-rtx3080",
        "category": "gpu",
        "title": "RTX 3080 Thermal VRAM Inspection",
        "body": "EVGA FTW3 VRAM thermal pads require check on high loads.",
        "metadata": {"generation": "Ampere", "critical_check": "VRAM temperature under load"}
    }
]
with open("fixtures/product_knowledge/knowledge_v1.json", "w") as f:
    json.dump(pk, f, indent=2)

# Ground Truth
gt = {
    "normalization_cases": [
        {"raw_title": "RTX 3080 EVGA FTW3", "expected_product_id": "prod-gpu-rtx3080"}
    ],
    "comparable_cases": [],
    "market_stats_cases": [
        {"product_id": "prod-gpu-rtx3080", "expected_median": 2600.0, "tolerance": 0.1}
    ],
    "retrieval_cases": [],
    "opportunity_cases": []
}
with open("fixtures/evals/ground_truth_v1.json", "w") as f:
    json.dump(gt, f, indent=2)

# HTML search fixture
html_search = """<html><body>
<div class="listing" data-id="101">
    <a href="/items/101.html">RTX 3080</a>
    <span class="price">R$ 2100</span>
</div>
</body></html>"""
with open("fixtures/marketplace/html/search.html", "w") as f:
    f.write(html_search)

# HTML detail fixture
html_detail = """<html><body>
<h1 id="title">RTX 3080 EVGA FTW3</h1>
<div id="price">2100.00</div>
<div id="description">Used for gaming</div>
<div id="seller">TechTrader_SP</div>
</body></html>"""
with open("fixtures/marketplace/html/items/101.html", "w") as f:
    f.write(html_detail)

print("Fixtures created.")
