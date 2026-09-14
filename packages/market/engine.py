import numpy as np
from typing import List, Dict, Any, Optional

def compute_market_stats(
    active_prices: List[float],
    disappeared_prices: Optional[List[float]] = None,
    durations_days: Optional[List[float]] = None,
    window_days: int = 30
) -> Dict[str, Any]:
    valid_active = [float(p) for p in (active_prices or []) if p is not None and np.isfinite(p) and float(p) > 0]
    valid_disappeared = [float(p) for p in (disappeared_prices or []) if p is not None and np.isfinite(p) and float(p) > 0]
    all_prices = valid_active + valid_disappeared
    if not all_prices:
        return {
            "sample_size": 0,
            "active_count": 0,
            "disappeared_count": 0,
            "asking_median": 0.0,
            "estimated_clearing_value": 0.0,
            "fast_sale_value": 0.0,
            "robust_center": 0.0,
            "p10": 0.0,
            "p25": 0.0,
            "median": 0.0,
            "p75": 0.0,
            "p90": 0.0,
            "mad": 0.0,
            "market_heat": 0.0,
            "heat_band": "COLD",
            "confidence": 0.0,
            "listing_velocity": 0.0,
            "disappearance_velocity": 0.0,
            "median_visible_duration_days": 0.0,
            "price_trend_30d": 0.0,
        }

    # Filter extreme noise/outliers (e.g. R$ 1 or R$ 999999) from statistical boundaries
    if len(all_prices) >= 8:
        q25_pre = float(np.percentile(all_prices, 25))
        q75_pre = float(np.percentile(all_prices, 75))
        iqr_pre = q75_pre - q25_pre
        if iqr_pre > 0:
            lower_bound = max(1.0, q25_pre - 2.5 * iqr_pre)
            upper_bound = q75_pre + 2.5 * iqr_pre
            clean_prices = [p for p in all_prices if lower_bound <= p <= upper_bound]
            if len(clean_prices) >= 4:
                all_prices = clean_prices
                if valid_active:
                    clean_active = [p for p in valid_active if lower_bound <= p <= upper_bound]
                    if clean_active:
                        valid_active = clean_active
                if valid_disappeared:
                    clean_dis = [p for p in valid_disappeared if lower_bound <= p <= upper_bound]
                    if clean_dis:
                        valid_disappeared = clean_dis

    arr_all = np.array(all_prices, dtype=float)
    arr_active = np.array(valid_active, dtype=float) if valid_active else arr_all
    arr_dis = np.array(valid_disappeared, dtype=float) if valid_disappeared else np.array([], dtype=float)

    median_all = float(np.median(arr_all))
    asking_median = float(np.median(arr_active)) if len(arr_active) > 0 else median_all
    
    # MAD (Median Absolute Deviation)
    mad = float(np.median(np.abs(arr_all - median_all)))
    
    p10 = float(np.percentile(arr_all, 10))
    p25 = float(np.percentile(arr_all, 25))
    p75 = float(np.percentile(arr_all, 75))
    p90 = float(np.percentile(arr_all, 90))

    # Robust center: mean of interquartile range (p25 to p75)
    iqr_mask = (arr_all >= p25) & (arr_all <= p75)
    robust_center = float(np.mean(arr_all[iqr_mask])) if np.any(iqr_mask) else median_all

    # Clearing value: if disappeared prices exist, use their median/p45 bounded by all prices
    if len(arr_dis) > 0:
        raw_clearing = float(np.percentile(arr_dis, 50))
        estimated_clearing_value = min(asking_median, max(p10, raw_clearing))
    else:
        estimated_clearing_value = float(np.percentile(arr_all, 35))

    # Fast sale value: price point where fast clearance happens (always below clearing value)
    raw_fast_sale = float(np.percentile(arr_all, 18))
    fast_sale_value = min(raw_fast_sale, estimated_clearing_value * 0.92)

    active_count = len(active_prices)
    disappeared_count = len(disappeared_prices or [])
    sample_size = len(all_prices)

    listing_velocity = round(active_count / max(1, window_days), 2)
    disappearance_velocity = round(disappeared_count / max(1, window_days), 2)

    median_visible_duration = float(np.median(durations_days)) if durations_days else 4.5

    # Price trend approximation
    price_trend_30d = -0.025 if disappearance_velocity > listing_velocity else 0.015

    # Market heat calculation
    market_heat, heat_band = compute_market_heat(
        active_count=active_count,
        disappeared_count=disappeared_count,
        median_duration=median_visible_duration
    )

    # Confidence based on sample size and dispersion
    confidence = min(0.96, max(0.40, (sample_size / 25.0) * (1.0 - min(0.4, mad / max(1.0, median_all)))))

    return {
        "sample_size": sample_size,
        "active_count": active_count,
        "disappeared_count": disappeared_count,
        "asking_median": round(asking_median, 2),
        "estimated_clearing_value": round(estimated_clearing_value, 2),
        "fast_sale_value": round(fast_sale_value, 2),
        "robust_center": round(robust_center, 2),
        "p10": round(p10, 2),
        "p25": round(p25, 2),
        "median": round(median_all, 2),
        "p75": round(p75, 2),
        "p90": round(p90, 2),
        "mad": round(mad, 2),
        "market_heat": round(market_heat, 1),
        "heat_band": heat_band,
        "confidence": round(confidence, 2),
        "listing_velocity": listing_velocity,
        "disappearance_velocity": disappearance_velocity,
        "median_visible_duration_days": round(median_visible_duration, 1),
        "price_trend_30d": round(price_trend_30d, 3),
    }

def compute_market_heat(active_count: int, disappeared_count: int, median_duration: float = 5.0) -> tuple[float, str]:
    total = active_count + disappeared_count
    if total == 0:
        return 20.0, "COLD"
    
    turnover_ratio = disappeared_count / max(1, total)
    duration_speed = max(0.0, min(1.0, (14.0 - median_duration) / 10.0))
    
    # 0-100 score
    raw_heat = (turnover_ratio * 60.0) + (duration_speed * 40.0)
    heat_score = float(max(0.0, min(100.0, raw_heat)))
    
    if heat_score >= 70.0:
        band = "HOT"
    elif heat_score >= 35.0:
        band = "NORMAL"
    else:
        band = "COLD"
        
    return round(heat_score, 1), band

def compute_opportunity_score(
    listing_price: float,
    market_stats: Dict[str, Any],
    preferences: Dict[str, Any],
    condition: str = "good",
    freshness_days: float = 2.0,
    is_preferred_location: bool = True,
    risk_factors: Optional[List[str]] = None
) -> tuple[float, Dict[str, float], float, str]:
    clearing = market_stats.get("estimated_clearing_value", 0.0)
    if clearing <= 0 or listing_price <= 0:
        return 0.0, {}, 0.0, "No market clearing reference available."

    # Price edge relative to clearing value
    edge = (clearing - listing_price) / clearing
    
    # 1. Price edge component (0 to 40 pts)
    if edge <= 0:
        price_edge_component = 0.0
    else:
        price_edge_component = min(40.0, edge * 160.0) # 25% edge = 40 pts

    # 2. Liquidity component (0 to 25 pts)
    heat = market_stats.get("market_heat", 50.0)
    liquidity_component = min(25.0, (heat / 100.0) * 25.0)

    # 3. Condition component (0 to 15 pts)
    condition_weights = {
        "new": 15.0,
        "like_new": 15.0,
        "good": 11.0,
        "fair": 6.0,
        "for_parts": 0.0
    }
    condition_component = condition_weights.get(condition.lower(), 10.0)

    # 4. Personal fit component (0 to 15 pts)
    category = market_stats.get("category", "gpu")
    expertise_map = preferences.get("category_expertise", {})
    expertise = expertise_map.get(category, 0.8)
    personal_fit_component = min(15.0, expertise * 15.0)

    # 5. Freshness component (0 to 10 pts)
    freshness_component = max(0.0, min(10.0, 10.0 - (freshness_days * 0.7)))

    # 6. Convenience component (0 to 5 pts)
    convenience_component = 5.0 if is_preferred_location else 2.0

    # 7. Risk penalty (0 to 40 pts)
    risk_penalty = 0.0
    if edge > 0.60:  # Suspiciously cheap (>60% under clearing) -> high scam likelihood
        risk_penalty += 35.0
    elif edge > 0.45:
        risk_penalty += 15.0

    if condition.lower() == "for_parts":
        risk_penalty += 20.0

    if risk_factors:
        risk_penalty += len(risk_factors) * 8.0

    # Capital constraint
    max_capital = preferences.get("max_capital", 99999.0)
    if listing_price > max_capital:
        risk_penalty += 50.0

    # Desired min edge constraint
    min_desired_edge = preferences.get("min_desired_edge", 0.10)
    if edge < min_desired_edge:
        price_edge_component *= 0.5

    raw_score = (
        price_edge_component +
        liquidity_component +
        condition_component +
        personal_fit_component +
        freshness_component +
        convenience_component -
        risk_penalty
    )

    final_score = float(max(0.0, min(100.0, raw_score)))

    score_breakdown = {
        "price_edge_component": round(price_edge_component, 1),
        "liquidity_component": round(liquidity_component, 1),
        "condition_component": round(condition_component, 1),
        "personal_fit_component": round(personal_fit_component, 1),
        "freshness_component": round(freshness_component, 1),
        "convenience_component": round(convenience_component, 1),
        "risk_penalty": round(risk_penalty, 1)
    }

    if edge >= 0.15 and final_score >= 70:
        explanation = f"Price is {round(edge*100, 1)}% below estimated clearing value with strong market liquidity."
    elif edge > 0:
        explanation = f"Moderate edge of {round(edge*100, 1)}% against market clearing value."
    else:
        explanation = "Asking price is at or above market clearing reference."

    return round(final_score, 1), score_breakdown, round(edge, 3), explanation

def run_simulated_investigation(
    listing_dict: Dict[str, Any],
    product_dict: Dict[str, Any],
    market_stats: Dict[str, Any],
    knowledge_snippets: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    price = listing_dict.get("price", 0.0)
    clearing = market_stats.get("estimated_clearing_value", 0.0)
    edge = (clearing - price) / clearing if clearing > 0 else 0.0
    
    evidence = [
        f"Asking price R$ {price:.2f} compared to 30d clearing median of R$ {clearing:.2f} (edge: {edge*100:.1f}%)",
        f"Location verified in {listing_dict.get('location_city', 'Local')}, {listing_dict.get('location_state', 'SP')}",
        f"Market velocity shows {market_stats.get('median_visible_duration_days', 4.2)} days median visible duration"
    ]
    
    risks = []
    if edge > 0.50:
        risks.append("Exceptionally low price: verify hardware authenticity and request video benchmark")
    if listing_dict.get("condition") == "fair":
        risks.append("Fair condition: inspect physical ports, cooling fans, and cosmetic wear")
    if not risks:
        risks.append("Standard used hardware inspection recommended before finalizing payment")

    tool_trace = [
        {
            "tool_name": "get_market_snapshot",
            "input": {"product_id": product_dict.get("id")},
            "output": {
                "median": market_stats.get("median"),
                "clearing_value": clearing,
                "sample_size": market_stats.get("sample_size")
            }
        },
        {
            "tool_name": "search_comparables",
            "input": {"product_id": product_dict.get("id"), "tier": 1},
            "output": {
                "count": market_stats.get("sample_size", 10),
                "active_count": market_stats.get("active_count", 5)
            }
        }
    ]

    if knowledge_snippets:
        top_snippet = knowledge_snippets[0].get("body", "")
        evidence.append(f"Diagnostic note: {top_snippet[:90]}...")
        tool_trace.append({
            "tool_name": "retrieve_product_knowledge",
            "input": {"query": f"{product_dict.get('display_name')} common defects"},
            "output": {"matches": len(knowledge_snippets), "top_title": knowledge_snippets[0].get("title")}
        })

    verdict = "ATTRACTIVE_OPPORTUNITY" if edge >= 0.15 else "FAIR_MARKET_PRICE" if edge >= 0 else "OVERPRICED"

    return {
        "status": "completed",
        "verdict": verdict,
        "confidence": market_stats.get("confidence", 0.90),
        "key_evidence": evidence,
        "risks": risks,
        "market_summary": f"Market band is {market_stats.get('heat_band', 'NORMAL')} with sample confidence of {int(market_stats.get('confidence', 0.9)*100)}%.",
        "suggested_next_checks": [
            "Request functional stress-test screenshot or video",
            "Confirm original accessories or invoice match"
        ],
        "tool_trace": tool_trace
    }


def calculate_market_snapshot_from_snapshots(
    db: Any,
    *,
    market_cohort_id: Optional[str] = None,
    product_id: Optional[str] = None,
    category: str = "gpu",
    source: str = "fixture",
    pipeline_run_id: Optional[str] = None,
    window_days: int = 30,
    evidence_tier: str = "audited",
) -> Any:
    """Calculate and persist MarketSnapshot and its MarketSnapshotMember records from ListingSnapshots."""
    import datetime
    from packages.core.hardware_taxonomy import HARDWARE_TAXONOMY_VERSION, ClassificationStatus
    from packages.core.models import ListingSnapshot, MarketSnapshot, MarketSnapshotMember

    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    window_start = now - datetime.timedelta(days=window_days)

    from sqlalchemy import or_

    # Query all snapshots within window
    query = db.query(ListingSnapshot).filter(
        ListingSnapshot.observed_at >= window_start,
    )
    if evidence_tier == "audited":
        query = query.filter(ListingSnapshot.evidence_level == "detail")
    if product_id and market_cohort_id:
        query = query.filter(
            or_(
                ListingSnapshot.product_id == product_id,
                ListingSnapshot.market_cohort_id == market_cohort_id,
            )
        )
    elif product_id:
        query = query.filter(ListingSnapshot.product_id == product_id)
    elif market_cohort_id:
        query = query.filter(ListingSnapshot.market_cohort_id == market_cohort_id)
    elif category:
        query = query.filter(ListingSnapshot.category == category)

    all_snapshots = query.order_by(ListingSnapshot.observed_at.desc()).all()

    # Deduplicate by latest snapshot per listing_id
    latest_per_listing: Dict[str, ListingSnapshot] = {}
    for snap in all_snapshots:
        current = latest_per_listing.get(snap.listing_id)
        if current is None:
            latest_per_listing[snap.listing_id] = snap
            continue
        # A detailed observation is always stronger evidence than a newer
        # search-card refresh of the same marketplace listing.
        if evidence_tier == "preliminary" and snap.evidence_level == "detail" and current.evidence_level != "detail":
            latest_per_listing[snap.listing_id] = snap

    active_prices: List[float] = []
    disappeared_prices: List[float] = []
    durations_days: List[float] = []

    included_count = 0
    unverified_count = 0
    excluded_count = 0
    exclusion_summary: Dict[str, int] = {}
    members_to_create: List[Dict[str, Any]] = []

    for listing_id, snap in latest_per_listing.items():
        is_eligible = bool(getattr(snap, "analytics_eligible", False))
        status_val = getattr(snap, "classification_status", ClassificationStatus.UNVERIFIED.value)
        status_str = status_val.value if hasattr(status_val, "value") else str(status_val)
        is_confirmed = status_str == ClassificationStatus.CONFIRMED.value
        exclusion_codes = list(getattr(snap, "exclusion_codes", []) or [])

        # Preliminary statistics intentionally accept card evidence, but only
        # when the projection marked it eligible.  Audited snapshots retain
        # the stricter historical contract.
        if is_eligible and is_confirmed and snap.price is not None and snap.price > 0:
            included_count += 1
            role = "active" if snap.status == "active" else "disappeared"
            if role == "active":
                active_prices.append(float(snap.price))
            else:
                disappeared_prices.append(float(snap.price))
            durations_days.append(4.0)

            members_to_create.append({
                "listing_snapshot_id": snap.id,
                "included": True,
                "role": role,
                "exclusion_code": None,
            })
        else:
            if status_str in (ClassificationStatus.UNVERIFIED.value, ClassificationStatus.LEGACY_UNVERIFIED.value):
                unverified_count += 1
            else:
                excluded_count += 1

            primary_exclusion = exclusion_codes[0] if exclusion_codes else "UNVERIFIED_EVIDENCE"
            exclusion_summary[primary_exclusion] = exclusion_summary.get(primary_exclusion, 0) + 1

            members_to_create.append({
                "listing_snapshot_id": snap.id,
                "included": False,
                "role": "excluded",
                "exclusion_code": primary_exclusion,
            })

    stats = compute_market_stats(
        active_prices=active_prices,
        disappeared_prices=disappeared_prices,
        durations_days=durations_days,
        window_days=window_days,
    )

    snapshot = MarketSnapshot(
        product_id=product_id,
        market_cohort_id=market_cohort_id,
        category=category,
        source=source,
        evidence_tier=evidence_tier,
        pipeline_run_id=pipeline_run_id,
        taxonomy_version=HARDWARE_TAXONOMY_VERSION,
        window_days=window_days,
        window_start=window_start,
        window_end=now,
        sample_size=len(latest_per_listing),
        active_count=stats["active_count"],
        disappeared_count=stats["disappeared_count"],
        included_count=included_count,
        unverified_count=unverified_count,
        excluded_count=excluded_count,
        exclusion_summary=exclusion_summary,
        is_legacy=False,
        asking_median=stats["asking_median"],
        estimated_clearing_value=stats["estimated_clearing_value"],
        fast_sale_value=stats["fast_sale_value"],
        robust_center=stats["robust_center"],
        p10=stats["p10"],
        p25=stats["p25"],
        median=stats["median"],
        p75=stats["p75"],
        p90=stats["p90"],
        mad=stats["mad"],
        market_heat=stats["market_heat"],
        heat_band=stats["heat_band"],
        confidence=stats["confidence"],
        listing_velocity=stats["listing_velocity"],
        disappearance_velocity=stats["disappearance_velocity"],
        median_visible_duration_days=stats["median_visible_duration_days"],
        price_trend_30d=stats["price_trend_30d"],
        calculated_at=now,
    )
    db.add(snapshot)
    db.flush()

    # Add member records
    for mem_data in members_to_create:
        mem = MarketSnapshotMember(
            market_snapshot_id=snapshot.id,
            listing_snapshot_id=mem_data["listing_snapshot_id"],
            included=mem_data["included"],
            role=mem_data["role"],
            exclusion_code=mem_data["exclusion_code"],
            created_at=now,
        )
        db.add(mem)

    db.flush()
    return snapshot
