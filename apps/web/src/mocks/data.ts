export const mockDashboardData = {
  active_listing_count: 182,
  opportunity_count: 14,
  pipeline_runs_count: 5,
  latest_pipeline_status: "completed",
  top_market_heat: [
    {
      product_id: "prod-gpu-rtx3080",
      product_name: "NVIDIA GeForce RTX 3080 10GB",
      category: "gpu",
      heat_score: 84.5,
      heat_band: "HOT",
      sample_size: 28,
      median_price: 2850.0
    }
  ],
  top_opportunities: [
    {
      id: "opp-001",
      listing_id: "list-101",
      product_id: "prod-gpu-rtx3080",
      product_name: "NVIDIA GeForce RTX 3080 10GB",
      category: "gpu",
      asking_price: 2100.0,
      estimated_clearing_value: 2750.0,
      fast_sale_value: 2400.0,
      price_edge: 0.236,
      final_score: 88.4,
      market_heat: 84.5,
      heat_band: "HOT",
      confidence: 0.92,
      condition: "like_new",
      location: "São Paulo, SP",
      first_seen: "2026-08-20T10:00:00Z"
    }
  ],
  recent_pipelines: [
    {
      id: "pipe-001",
      type: "synthetic_full_ingest",
      status: "completed",
      started_at: "2026-08-23T15:30:00Z",
      finished_at: "2026-08-23T15:30:12Z",
      processed_count: 240,
      opportunities_found: 14
    }
  ],
  market_trend_summary: {
    avg_price_change_7d: -0.024,
    hot_categories: ["gpu", "notebook"],
    cold_categories: ["motherboard"]
  }
};
