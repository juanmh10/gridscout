import os
import json
import random
import numpy as np
from datetime import datetime, timedelta, timezone
from packages.core.database import SessionLocal, engine, Base
from packages.core.models import (
    Product, Listing, ListingSnapshot, ProductKnowledge, 
    UserPreference, MarketSnapshot, Opportunity, PipelineRun, EvalRun, Profile
)
from packages.market.engine import (
    compute_market_stats, compute_opportunity_score, run_simulated_investigation
)
from packages.retrieval.embeddings import create_deterministic_embedding

def generate_seed(force: bool = False):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    if force:
        # Clear existing data when explicitly forced
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(table.delete())
        db.commit()
    elif db.query(PipelineRun).filter(PipelineRun.id == "pipe-001").first():
        result = {
            "listings": db.query(Listing).count(),
            "products": db.query(Product).count(),
            "opportunities": db.query(Opportunity).count(),
        }
        db.close()
        return result

    random.seed(42)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. User Preference
    pref_dict = {
        "target_categories": ["gpu", "notebook", "cpu", "ram", "ssd"],
        "category_expertise": {
            "gpu": 1.0,
            "notebook": 0.85,
            "cpu": 0.9,
            "ram": 0.7,
            "ssd": 0.7,
            "motherboard": 0.6
        },
        "max_capital": 5000.0,
        "min_desired_edge": 0.10,
        "risk_tolerance": 0.45,
        "preferred_location": "SP"
    }
    migrated_profile = Profile(
        id="profile-migrated",
        name="Perfil migrado",
        name_normalized="perfil migrado",
        preferences=pref_dict,
        created_at=now,
        updated_at=now,
    )
    db.add(migrated_profile)
    user_pref = UserPreference(profile_id=migrated_profile.id, preferences=pref_dict)
    db.add(user_pref)

    # 2. Canonical Products
    products_def = [
        # GPUs
        ("prod-gpu-rtx3060", "gpu", "NVIDIA", "GeForce RTX 30 Series", "RTX 3060", "12GB", "NVIDIA GeForce RTX 3060 12GB", {"memory_gb": 12, "tdp": 170}, 1500.0),
        ("prod-gpu-rtx3070", "gpu", "NVIDIA", "GeForce RTX 30 Series", "RTX 3070", "8GB", "NVIDIA GeForce RTX 3070 8GB", {"memory_gb": 8, "tdp": 220}, 2000.0),
        ("prod-gpu-rtx3080", "gpu", "NVIDIA", "GeForce RTX 30 Series", "RTX 3080", "10GB", "NVIDIA GeForce RTX 3080 10GB", {"memory_gb": 10, "tdp": 320}, 2750.0),
        ("prod-gpu-rtx3080-12g", "gpu", "NVIDIA", "GeForce RTX 30 Series", "RTX 3080", "12GB", "NVIDIA GeForce RTX 3080 12GB", {"memory_gb": 12, "tdp": 350}, 3000.0),
        ("prod-gpu-rtx3080ti", "gpu", "NVIDIA", "GeForce RTX 30 Series", "RTX 3080 Ti", "12GB", "NVIDIA GeForce RTX 3080 Ti 12GB", {"memory_gb": 12, "tdp": 350}, 3300.0),
        ("prod-gpu-rx6700xt", "gpu", "AMD", "Radeon RX 6000", "RX 6700 XT", "12GB", "AMD Radeon RX 6700 XT 12GB", {"memory_gb": 12, "tdp": 230}, 1800.0),
        ("prod-gpu-rx6800xt", "gpu", "AMD", "Radeon RX 6000", "RX 6800 XT", "16GB", "AMD Radeon RX 6800 XT 16GB", {"memory_gb": 16, "tdp": 300}, 2600.0),
        
        # Notebooks
        ("prod-nb-macbook-m1", "notebook", "Apple", "MacBook Air", "M1 2020", "8GB/256GB", "Apple MacBook Air M1 8GB 256GB", {"chip": "M1", "ram_gb": 8, "storage_gb": 256}, 4200.0),
        ("prod-nb-macbook-m1-16", "notebook", "Apple", "MacBook Air", "M1 2020", "16GB/512GB", "Apple MacBook Air M1 16GB 512GB", {"chip": "M1", "ram_gb": 16, "storage_gb": 512}, 5200.0),
        ("prod-nb-dell-g15", "notebook", "Dell", "G Series", "G15 5511", "RTX 3060", "Dell G15 Core i7 RTX 3060", {"cpu": "i7-11800H", "gpu": "RTX 3060"}, 3800.0),
        
        # CPUs
        ("prod-cpu-5600x", "cpu", "AMD", "Ryzen 5000", "Ryzen 5 5600X", None, "AMD Ryzen 5 5600X 6-Core", {"cores": 6, "socket": "AM4"}, 750.0),
        ("prod-cpu-5800x3d", "cpu", "AMD", "Ryzen 5000", "Ryzen 7 5800X3D", None, "AMD Ryzen 7 5800X3D 8-Core", {"cores": 8, "socket": "AM4"}, 1700.0),
        ("prod-cpu-12400f", "cpu", "Intel", "Core 12th Gen", "Core i5-12400F", None, "Intel Core i5-12400F 6-Core", {"cores": 6, "socket": "LGA1700"}, 650.0),
        ("prod-cpu-13700k", "cpu", "Intel", "Core 13th Gen", "Core i7-13700K", None, "Intel Core i7-13700K 16-Core", {"cores": 16, "socket": "LGA1700"}, 2100.0),
    ]

    prods_map = {}
    base_prices = {}
    for pid, cat, brand, fam, model, var, dname, attrs, bprice in products_def:
        p = Product(
            id=pid,
            category=cat,
            brand=brand,
            family=fam,
            model=model,
            variant=var,
            display_name=dname,
            attributes=attrs,
            canonical_tier=1
        )
        db.add(p)
        prods_map[pid] = p
        base_prices[pid] = bprice

    db.commit()

    from packages.catalog.gpu import ensure_gpu_catalog
    from packages.catalog.cpu import ensure_cpu_catalog
    from packages.catalog.ram import ensure_ram_catalog
    from packages.catalog.notebooks import ensure_notebook_catalog

    ensure_gpu_catalog(db)
    ensure_cpu_catalog(db)
    ensure_ram_catalog(db)
    try:
        ensure_notebook_catalog(db)
    except Exception:
        pass
    from packages.catalog.resolver import resolve_product_and_cohort
    for pid, p in prods_map.items():
        _, cohort, _ = resolve_product_and_cohort(
            db, category=p.category, title=p.display_name, attributes=p.attributes
        )
        if cohort:
            p.market_cohort_id = cohort.id
    db.commit()

    # 3. Product Knowledge (Diagnostic notes & common defects)
    pk_items = [
        ("pk-gpu-rtx3080-01", "prod-gpu-rtx3080", "gpu", "NVIDIA RTX 3080 Thermal VRAM Inspection", 
         "Early batches of GDDR6X cards run hot. Ensure VRAM thermal pads have not deteriorated or leaked silicon oil. Benchmark under FurMark/TimeSpy."),
        ("pk-gpu-rtx3070-01", "prod-gpu-rtx3070", "gpu", "RTX 3070 Power Connector & Fan Bearings", 
         "Check dual 8-pin PCIe power socket for heat discoloration. Listen for bearing rattle on dual-fan models."),
        ("pk-nb-macbook-m1-01", "prod-nb-macbook-m1", "notebook", "MacBook Air M1 Battery Health & Display Flex", 
         "Inspect battery cycle count (healthy under 300 cycles / >85% capacity). Verify screen coating has no keyboard imprint."),
        ("pk-cpu-5800x3d-01", "prod-cpu-5800x3d", "cpu", "Ryzen 5800X3D Pin Straightness & Cooler Mounting", 
         "Inspect AM4 gold pins thoroughly under magnification for bending. Check IHS for deep liquid-metal pitting.")
    ]

    for pkid, prod_id, cat, title, body in pk_items:
        emb = create_deterministic_embedding(f"{title} {body}")
        pk = ProductKnowledge(
            id=pkid,
            product_id=prod_id,
            category=cat,
            title=title,
            body=body,
            metadata_json={"source": "hardware_diagnostic_v1"},
            embedding=emb
        )
        db.add(pk)
    db.commit()

    # 4. Generate ~200 Synthetic Listings & Snapshots (30-day window)
    cities = [
        ("São Paulo", "SP"), ("Campinas", "SP"), ("Santos", "SP"),
        ("Rio de Janeiro", "RJ"), ("Curitiba", "PR"), ("Belo Horizonte", "MG")
    ]
    conditions = ["like_new", "good", "good", "good", "fair", "new"]

    listings = []
    prod_ids = list(prods_map.keys())

    for i in range(1, 201):
        pid = random.choice(prod_ids)
        prod = prods_map[pid]
        base_p = base_prices[pid]
        city, state = random.choice(cities)
        cond = random.choice(conditions)

        rand_type = random.random()
        if rand_type < 0.03: # scam outlier
            price = round(base_p * random.uniform(0.15, 0.35), 2)
            title = f"{prod.display_name} - URGENTE CHAMA NO WHATS"
            desc = "Vendo urgente motivo de viagem. Tratar no whats 11999999999. Nao aceito olx pay."
            cond = "good"
            is_fast_disappear = True
        elif rand_type < 0.13: # Attractive bargain
            price = round(base_p * random.uniform(0.68, 0.78), 2)
            title = f"{prod.display_name} Impecável na Caixa c/ NF"
            desc = "Usado com muito cuidado, com nota fiscal e caixa original completa. Funcionando 100%."
            cond = "like_new"
            is_fast_disappear = True
        elif rand_type < 0.18: # For parts / fair
            price = round(base_p * random.uniform(0.40, 0.55), 2)
            title = f"{prod.display_name} Para Conserto / Peças"
            desc = "Apresenta artefatos ou defeito na tela. Para quem entende de reparo."
            cond = "for_parts"
            is_fast_disappear = False
        else: # Standard market listing
            price = round(base_p * random.uniform(0.85, 1.25), 2)
            title = f"{prod.display_name} Usado Ótimo Estado"
            desc = "Peça funcionando perfeitamente, sem detalhes."
            is_fast_disappear = False

        days_ago = random.randint(1, 30)
        first_seen = now - timedelta(days=days_ago)

        if is_fast_disappear:
            active_duration = random.randint(1, 4)
            is_active = (days_ago - active_duration) <= 0
            last_seen = first_seen + timedelta(days=min(days_ago, active_duration))
            status = "active" if is_active else "disappeared"
        else:
            status = "disappeared" if (days_ago > 18 and random.random() < 0.5) else "active"
            last_seen = now if status == "active" else first_seen + timedelta(days=random.randint(5, 15))

        listing_id = f"list-{i:03d}"
        market_cohort_id = prod.market_cohort_id
        is_eligible = cond != "for_parts"
        exclusion_codes = ["PARTS_OR_BROKEN"] if cond == "for_parts" else []

        listing = Listing(
            id=listing_id,
            source="fixture",
            external_id=f"synth-{prod.category}-{i:03d}",
            product_id=pid,
            market_cohort_id=market_cohort_id,
            category=prod.category,
            item_form="standalone" if prod.category != "notebook" else "full_system",
            classification_status="confirmed",
            classification_confidence=0.95,
            taxonomy_version="hardware-taxonomy-v2",
            schema_version="hardware-schema-v2",
            analytics_eligible=is_eligible,
            exclusion_codes=exclusion_codes,
            classification_evidence=[f"Classificado como {prod.display_name}"],
            title=title,
            description=desc,
            price=price,
            location_state=state,
            location_city=city,
            seller_name=f"Vendedor_{city[:3]}_{i}",
            seller_rating=round(random.uniform(4.2, 5.0), 1),
            condition=cond,
            attributes={"tested": True, "shipping": random.choice(["available", "pickup_only"])},
            source_url=f"http://fixture.local/items/{i}.html",
            first_seen=first_seen,
            last_seen=last_seen,
            status=status,
            normalization_confidence=0.95
        )
        db.add(listing)
        listings.append(listing)

        # Generate temporal snapshots for this listing
        snap_days = sorted(set([0, max(1, days_ago // 2), days_ago]))
        curr_price = price
        for sd in snap_days:
            obs_time = first_seen + timedelta(days=sd)
            if obs_time > last_seen:
                break
            snap_status = "active" if (obs_time < last_seen or status == "active") else status
            if sd > 10 and not is_fast_disappear:
                curr_price = round(curr_price * 0.96, 2)
            snap = ListingSnapshot(
                listing_id=listing_id,
                product_id=pid,
                market_cohort_id=market_cohort_id,
                category=prod.category,
                item_form="standalone" if prod.category != "notebook" else "full_system",
                attributes={"tested": True},
                classification_status="confirmed",
                classification_confidence=0.95,
                taxonomy_version="hardware-taxonomy-v2",
                schema_version="hardware-schema-v2",
                analytics_eligible=is_eligible,
                exclusion_codes=exclusion_codes,
                classification_evidence=[f"Classificado como {prod.display_name}"],
                price=curr_price,
                status=snap_status,
                observed_at=obs_time
            )
            db.add(snap)

    db.commit()

    # 5. Compute Market Snapshots for each canonical product
    from packages.market.engine import calculate_market_snapshot_from_snapshots
    product_stats = {}
    market_snapshots_map = {}
    for pid in prod_ids:
        prod = prods_map[pid]
        ms = calculate_market_snapshot_from_snapshots(
            db,
            product_id=pid,
            market_cohort_id=prod.market_cohort_id,
            category=prod.category,
            source="fixture",
            window_days=30,
        )
        market_snapshots_map[pid] = ms
        product_stats[pid] = {
            "sample_size": ms.sample_size,
            "active_count": ms.active_count,
            "disappeared_count": ms.disappeared_count,
            "asking_median": ms.asking_median,
            "estimated_clearing_value": ms.estimated_clearing_value,
            "fast_sale_value": ms.fast_sale_value,
            "robust_center": ms.robust_center,
            "p10": ms.p10,
            "p25": ms.p25,
            "median": ms.median,
            "p75": ms.p75,
            "p90": ms.p90,
            "mad": ms.mad,
            "market_heat": ms.market_heat,
            "heat_band": ms.heat_band,
            "confidence": ms.confidence,
            "listing_velocity": ms.listing_velocity,
            "disappearance_velocity": ms.disappearance_velocity,
            "median_visible_duration_days": ms.median_visible_duration_days,
            "price_trend_30d": ms.price_trend_30d,
            "category": prod.category,
        }

    db.commit()

    # 6. Score Opportunities for Active Listings
    opp_count = 0
    for l in listings:
        if l.status != "active" or not l.product_id:
            continue
        prod = prods_map[l.product_id]
        stats = product_stats[l.product_id]
        freshness = (now - l.first_seen).total_seconds() / 86400.0
        is_pref_loc = (l.location_state == "SP")
        risk_flags = ["whatsapp_scam"] if "WHATS" in l.title else []

        score, breakdown, edge, expl = compute_opportunity_score(
            listing_price=l.price,
            market_stats=stats,
            preferences=pref_dict,
            condition=l.condition,
            freshness_days=freshness,
            is_preferred_location=is_pref_loc,
            risk_factors=risk_flags
        )

        if score >= 60.0 or edge >= 0.12:
            opp_count += 1
            pk_matches = [
                {"title": pk[2], "body": pk[3]} 
                for pk in pk_items if pk[1] == l.product_id
            ]
            investigation = run_simulated_investigation(
                listing_dict={"price": l.price, "location_city": l.location_city, "location_state": l.location_state, "condition": l.condition},
                product_dict={"id": prod.id, "display_name": prod.display_name},
                market_stats=stats,
                knowledge_snippets=pk_matches
            )

            ms = market_snapshots_map.get(l.product_id)
            latest_snap = db.query(ListingSnapshot).filter(ListingSnapshot.listing_id == l.id).order_by(ListingSnapshot.observed_at.desc()).first()

            opp = Opportunity(
                id=f"opp-{l.id}",
                listing_id=l.id,
                product_id=l.product_id,
                listing_snapshot_id=latest_snap.id if latest_snap else None,
                market_snapshot_id=ms.id if ms else None,
                market_cohort_id=prod.market_cohort_id,
                category=prod.category,
                price_edge=edge,
                final_score=score,
                market_heat=stats["market_heat"],
                heat_band=stats["heat_band"],
                confidence=stats["confidence"],
                score_breakdown=breakdown,
                explanation=expl,
                investigation=investigation,
                investigation_status="completed",
                computed_at=now
            )
            db.add(opp)

    # 7. Add Historical Pipeline Runs
    pipe = PipelineRun(
        id="pipe-001",
        profile_id=migrated_profile.id,
        type="synthetic_full_ingest",
        status="completed",
        started_at=now - timedelta(minutes=45),
        finished_at=now - timedelta(minutes=44, seconds=48),
        duration_seconds=12.2,
        processed_count=200,
        snapshots_created=480,
        products_normalized=200,
        opportunities_found=opp_count,
        steps=[
            {"name": "fixture_ingest", "status": "completed", "duration_seconds": 1.2, "items_in": 200, "items_out": 200},
            {"name": "product_normalization", "status": "completed", "duration_seconds": 2.5, "items_in": 200, "items_out": 200},
            {"name": "market_statistics", "status": "completed", "duration_seconds": 3.1, "items_in": len(prod_ids), "items_out": len(prod_ids)},
            {"name": "opportunity_scoring", "status": "completed", "duration_seconds": 2.4, "items_in": 200, "items_out": opp_count},
            {"name": "agent_investigation", "status": "completed", "duration_seconds": 3.0, "items_in": opp_count, "items_out": opp_count}
        ]
    )
    db.add(pipe)

    # 8. Add Benchmark Runs
    bench = EvalRun(
        id="eval-run-001",
        dataset_version="v1.0.0",
        configuration="deterministic_local_v1",
        executed_at=now - timedelta(minutes=20),
        status="completed",
        failures_count=0,
        metrics={
            "normalization_accuracy": 0.985,
            "comparable_precision_at_5": 0.960,
            "price_error_mae": 34.50,
            "retrieval_mrr": 0.940,
            "opportunity_precision_at_10": 0.920,
            "tool_success_rate": 1.0,
            "pipeline_duration_seconds": 12.2
        },
        details={"evaluated_cases": 60, "passed": 60}
    )
    db.add(bench)

    db.commit()
    db.close()
    return {"listings": len(listings), "products": len(prod_ids), "opportunities": opp_count}
