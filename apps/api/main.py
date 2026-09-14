import os
import re
import json
import datetime
import uuid
import unicodedata
import html
import numpy as np
from typing import Optional, List, Dict, Any, Literal
from fastapi import FastAPI, Depends, HTTPException, Query, Header, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, asc, and_, or_
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified
from packages.core.database import get_db
from packages.core.models import (
    Product, Listing, ListingSnapshot, MarketSnapshot, 
    Opportunity, PipelineRun, EvalRun, UserPreference, ProductKnowledge,
    SearchScope, PipelineRunScope, ModelMetricCall, Profile, SearchDefinition,
    SearchSession, SearchSessionMessage, OpportunityFeedback, PipelineDatasetAnalysis,
    ListingDiscovery, ListingEnrichmentTask, PipelineRetrievalTask,
    OpportunitySignal,
)
from packages.market.engine import (
    compute_market_stats, compute_opportunity_score, run_simulated_investigation
)
from packages.ai.model_gateway import get_model_gateway, VertexModelGateway, DeterministicLocalModelGateway
from packages.ai.genai_auth import configured_genai_auth_mode
from packages.ai.metrics import model_metric_context
from packages.retrieval.embeddings import get_embedding_provider, GeminiEmbeddingProvider, LocalHashEmbeddingProvider
from packages.retrieval.retriever import get_retriever, VertexSearchRetriever, PgVectorRetriever
from packages.pipeline.chat import PipelineChatError, PipelineChatService
from packages.pipeline.discovery_card_analysis import (
    _incomplete_cards_query,
    card_analysis_counts,
    latest_incomplete_run_id,
    queue_latest_incomplete_cards,
    retry_failed_run_cards,
)
from packages.core.urls import canonical_source_url

app = FastAPI(
    title="Market Radar API",
    version="1.0.0",
    description="Local & Vertex AI Market Intelligence API for Used Hardware and Consumer Technology"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------------------------
# 1. Health & Status
# ----------------------------------------------------------------------

@app.get("/api/v1/health")
def get_health(db: Session = Depends(get_db)):
    try:
        db.execute(func.now())
        db_status = "connected"
    except Exception:
        db_status = "error"
    return {
        "status": "ok",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "database": db_status,
        "build_sha": os.getenv("BUILD_SHA", "dev"),
        "parser_version": "olx-dom-2026-08-24",
        "alembic_head": "f9a0b1c2d3e4",
    }

@app.get("/api/v1/status")
def get_status():
    mg = get_model_gateway()
    ep = get_embedding_provider()
    ret = get_retriever()
    
    is_vertex_configured = bool(os.getenv("VERTEX_PROJECT_ID") or os.getenv("GEMINI_API_KEY"))
    app_mode = os.getenv("APP_MODE", "local")
    ext_enabled = os.getenv("EXTERNAL_PROVIDERS", "disabled").lower() == "enabled"

    return {
        "app_mode": app_mode,
        "synthetic_data": True if app_mode == "local" else False,
        "fixture_version": "v1.0.0",
        "external_providers": {
            "olx": "active" if (app_mode == "live" and ext_enabled) else "disabled",
            "vertex_ai": "active" if (isinstance(mg, VertexModelGateway) and is_vertex_configured and ext_enabled) else "disabled",
            "vertex_search": "active" if (isinstance(ret, VertexSearchRetriever) and is_vertex_configured and ext_enabled) else "disabled",
            "gemini_embeddings": "active" if (isinstance(ep, GeminiEmbeddingProvider) and is_vertex_configured and ext_enabled) else "disabled"
        },
        "active_adapters": {
            "marketplace": os.getenv("MARKETPLACE_SOURCE", "fixture"),
            "model": mg.__class__.__name__,
            "model_auth_mode": configured_genai_auth_mode(),
            "embedding": ep.__class__.__name__,
            "retriever": ret.__class__.__name__
        }
    }


@app.get("/api/v1/audit/taxonomy")
def get_taxonomy_audit(db: Session = Depends(get_db)):
    from packages.core.audit import run_database_audit
    return run_database_audit(db)


# ----------------------------------------------------------------------
# Profile ownership
# ----------------------------------------------------------------------

def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _normalise_profile_name(name: str) -> tuple[str, str]:
    """Return a display-safe name and its portable uniqueness key."""
    display_name = " ".join((name or "").strip().split())
    if not display_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "PROFILE_NAME_REQUIRED", "message": "Profile name is required"},
        )
    folded = unicodedata.normalize("NFKD", display_name)
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    return display_name, folded.casefold()


def _profile_dict(profile: Profile) -> dict:
    return {
        "id": profile.id,
        "name": profile.name,
        "preferences": profile.preferences or {},
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }


def _profile_error(code: str, message: str, status_code: int) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _resource_not_found(resource: str) -> HTTPException:
    # Do not distinguish a missing record from a record belonging to another
    # profile.  This is intentionally the same response for both cases.
    return _profile_error("RESOURCE_NOT_FOUND", f"{resource} not found", status.HTTP_404_NOT_FOUND)


def _require_profile(
    db: Session,
    x_profile_id: Optional[str],
) -> Profile:
    if not x_profile_id or not x_profile_id.strip():
        raise _profile_error(
            "PROFILE_REQUIRED",
            "X-Profile-ID header is required for this resource",
            status.HTTP_400_BAD_REQUEST,
        )
    profile = db.query(Profile).filter(Profile.id == x_profile_id.strip()).first()
    if not profile:
        raise _profile_error(
            "PROFILE_NOT_FOUND",
            "The requested profile does not exist",
            status.HTTP_404_NOT_FOUND,
        )
    return profile


class ProfileCreatePayload(BaseModel):
    name: str
    preferences: Dict[str, Any] = Field(default_factory=dict)


class ProfileRenamePayload(BaseModel):
    name: Optional[str] = None
    preferences: Optional[Dict[str, Any]] = None


@app.get("/api/v1/profiles")
def list_profiles(db: Session = Depends(get_db)):
    profiles = db.query(Profile).order_by(Profile.created_at.asc(), Profile.name.asc()).all()
    return {"items": [_profile_dict(profile) for profile in profiles]}


@app.post("/api/v1/profiles", status_code=status.HTTP_201_CREATED)
def create_profile(payload: ProfileCreatePayload, db: Session = Depends(get_db)):
    name, normalized = _normalise_profile_name(payload.name)
    now = _utcnow()
    profile = Profile(
        id=f"profile-{uuid.uuid4().hex[:12]}",
        name=name,
        name_normalized=normalized,
        preferences=payload.preferences or {},
        created_at=now,
        updated_at=now,
    )
    db.add(profile)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _profile_error(
            "PROFILE_NAME_CONFLICT",
            "A profile with this name already exists",
            status.HTTP_409_CONFLICT,
        ) from exc
    db.refresh(profile)
    return _profile_dict(profile)


@app.get("/api/v1/profiles/{profile_id}")
def get_profile(profile_id: str, db: Session = Depends(get_db)):
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not profile:
        raise _profile_error("PROFILE_NOT_FOUND", "The requested profile does not exist", status.HTTP_404_NOT_FOUND)
    return _profile_dict(profile)


@app.put("/api/v1/profiles/{profile_id}")
@app.patch("/api/v1/profiles/{profile_id}")
def rename_profile(profile_id: str, payload: ProfileRenamePayload, db: Session = Depends(get_db)):
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not profile:
        raise _profile_error("PROFILE_NOT_FOUND", "The requested profile does not exist", status.HTTP_404_NOT_FOUND)
    if payload.name is not None and payload.name.strip():
        name, normalized = _normalise_profile_name(payload.name)
        profile.name = name
        profile.name_normalized = normalized
    if payload.preferences is not None:
        profile.preferences = {**(profile.preferences or {}), **payload.preferences}
        flag_modified(profile, "preferences")
    profile.updated_at = _utcnow()
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _profile_error(
            "PROFILE_NAME_CONFLICT",
            "A profile with this name already exists",
            status.HTTP_409_CONFLICT,
        ) from exc
    db.refresh(profile)
    return _profile_dict(profile)

# ----------------------------------------------------------------------
# 2. Dashboard
# ----------------------------------------------------------------------

@app.get("/api/v1/dashboard")
def get_dashboard(
    category: Optional[str] = None,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    live_source = "olx" if os.getenv("APP_MODE", "local").lower() == "live" else None
    listing_filter = [Listing.status == "active"]
    opportunity_filter = []
    market_filter = []
    if category and category != "all":
        listing_filter.append(Listing.category == category)
        opportunity_filter.append(Opportunity.category == category)
        market_filter.append(MarketSnapshot.category == category)
    if live_source:
        listing_filter.append(Listing.source == live_source)
        opportunity_filter.extend([Opportunity.source == live_source, Opportunity.is_current.is_(True)])
        market_filter.append(MarketSnapshot.source == live_source)
    discovery_count = db.query(ListingDiscovery).join(PipelineRun, PipelineRun.id == ListingDiscovery.pipeline_run_id).filter(PipelineRun.profile_id == profile.id).count()
    signal_count = db.query(OpportunitySignal).filter(OpportunitySignal.profile_id == profile.id, OpportunitySignal.stage.in_(("preliminary", "queued_for_enrichment"))).count()
    active_count = db.query(Listing).filter(*listing_filter).count() + discovery_count
    opp_count = (db.query(Opportunity).filter(*opportunity_filter).count() if opportunity_filter else db.query(Opportunity).count()) + signal_count

    pipeline_query = db.query(PipelineRun).filter(PipelineRun.profile_id == profile.id)
    pipe_count = pipeline_query.count()
    latest_pipe = pipeline_query.order_by(desc(PipelineRun.started_at)).first()

    latest_market = (
        db.query(
            MarketSnapshot.product_id.label("product_id"),
            func.max(MarketSnapshot.calculated_at).label("latest_calculated_at"),
        )
        .filter(*market_filter)
        .group_by(MarketSnapshot.product_id)
        .subquery()
    )

    # Top market heat, using only the latest persisted snapshot per product.
    top_heat_records = (
        db.query(MarketSnapshot, Product)
        .join(Product, MarketSnapshot.product_id == Product.id)
        .join(
            latest_market,
            and_(
                latest_market.c.product_id == MarketSnapshot.product_id,
                latest_market.c.latest_calculated_at == MarketSnapshot.calculated_at,
            ),
        )
        .order_by(desc(MarketSnapshot.market_heat))
        .limit(5)
        .all()
    )
    top_market_heat = [
        {
            "product_id": ms.product_id,
            "product_name": prod.display_name,
            "category": prod.category,
            "heat_score": ms.market_heat,
            "heat_band": ms.heat_band,
            "sample_size": ms.sample_size,
            "median_price": ms.median
        }
        for ms, prod in top_heat_records
    ]

    # Top opportunities
    top_opp_records = (
        db.query(Opportunity, Listing, Product, MarketSnapshot)
        .join(Listing, Opportunity.listing_id == Listing.id)
        .join(Product, Opportunity.product_id == Product.id)
        .outerjoin(latest_market, latest_market.c.product_id == Product.id)
        .outerjoin(
            MarketSnapshot,
            and_(
                MarketSnapshot.product_id == Product.id,
                MarketSnapshot.calculated_at == latest_market.c.latest_calculated_at,
                *market_filter,
            ),
        )
        .filter(*opportunity_filter)
        .order_by(desc(Opportunity.final_score))
        .limit(6)
        .all()
    )
    top_opportunities = [
        {
            "id": opp.id,
            "listing_id": l.id,
            "product_id": prod.id,
            "product_name": prod.display_name,
            "category": prod.category,
            "asking_price": l.price,
            "estimated_clearing_value": ms.estimated_clearing_value if ms else l.price * 1.2,
            "fast_sale_value": ms.fast_sale_value if ms else l.price * 1.05,
            "price_edge": opp.price_edge,
            "final_score": opp.final_score,
            "market_heat": opp.market_heat,
            "heat_band": opp.heat_band,
            "confidence": opp.confidence,
            "condition": l.condition,
            "location": f"{l.location_city}, {l.location_state}",
            "first_seen": l.first_seen.isoformat() if l.first_seen else _utcnow().isoformat()
        }
        for opp, l, prod, ms in top_opp_records
    ]

    # Recent pipelines
    recent_pipes = pipeline_query.order_by(desc(PipelineRun.started_at)).limit(5).all()
    recent_pipelines = [
        {
            "id": p.id,
            "type": p.type,
            "status": p.status,
            "started_at": p.started_at.isoformat() if p.started_at else None,
            "finished_at": p.finished_at.isoformat() if p.finished_at else None,
            "processed_count": p.processed_count,
            "opportunities_found": p.opportunities_found
        }
        for p in recent_pipes
    ]

    return {
        "active_listing_count": active_count,
        "opportunity_count": opp_count,
        "pipeline_runs_count": pipe_count,
        "latest_pipeline_status": latest_pipe.status if latest_pipe else None,
        "top_market_heat": top_market_heat,
        "top_opportunities": top_opportunities,
        "recent_pipelines": recent_pipelines,
        "market_trend_summary": {
            "avg_price_change_7d": -0.024,
            "hot_categories": ["gpu", "notebook"],
            "cold_categories": ["motherboard"]
        }
    }

# ----------------------------------------------------------------------
# 3. Listings
# ----------------------------------------------------------------------

@app.get("/api/v1/listings")
def get_listings(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: Optional[str] = None,
    product_id: Optional[str] = None,
    market_cohort_id: Optional[str] = None,
    quality: Optional[str] = Query("all", description="Filter by classification quality: confirmed, unverified, all"),
    status: Optional[str] = "active",
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    search: Optional[str] = None,
    source: Optional[str] = None,
    audit_status: Optional[Literal["all", "unaudited", "audited"]] = "all",
    db: Session = Depends(get_db)
):
    query = db.query(Listing, Product).outerjoin(Product, Listing.product_id == Product.id)

    if category and category != "all":
        query = query.filter(Listing.category == category)
    if product_id:
        query = query.filter(Listing.product_id == product_id)
    if market_cohort_id:
        query = query.filter(Listing.market_cohort_id == market_cohort_id)
    if quality == "confirmed":
        query = query.filter(Listing.classification_status == "confirmed")
    elif quality == "unverified":
        query = query.filter(Listing.classification_status.in_(["unverified", "legacy_unverified"]))
    if status and status != "all":
        query = query.filter(Listing.status == status)
    if min_price is not None:
        query = query.filter(Listing.price >= min_price)
    if max_price is not None:
        query = query.filter(Listing.price <= max_price)
    if search:
        query = query.filter(Listing.title.ilike(f"%{search}%"))
    if source:
        query = query.filter(Listing.source == source)
    if audit_status and audit_status != "all":
        query = query.filter(Listing.audit_status == audit_status)

    total = query.count()
    records = query.order_by(desc(Listing.first_seen)).offset((page - 1) * page_size).limit(page_size).all()

    items = []
    for l, prod in records:
        snap_count = db.query(ListingSnapshot).filter(ListingSnapshot.listing_id == l.id).count()
        cohort_name = l.market_cohort.display_name if l.market_cohort else (prod.market_cohort.display_name if prod and prod.market_cohort else None)
        items.append({
            "id": l.id,
            "source": l.source,
            "external_id": l.external_id,
            "product_id": l.product_id,
            "product_name": prod.display_name if prod else (l.title or "Unknown"),
            "market_cohort_id": l.market_cohort_id or (prod.market_cohort_id if prod else None),
            "market_cohort_name": cohort_name,
            "category": l.category or (prod.category if prod else "other"),
            "item_form": getattr(l, "item_form", "standalone"),
            "classification_status": getattr(l, "classification_status", "unverified"),
            "classification_confidence": getattr(l, "classification_confidence", 0.0),
            "analytics_eligible": getattr(l, "analytics_eligible", False),
            "exclusion_codes": getattr(l, "exclusion_codes", []) or [],
            "classification_evidence": getattr(l, "classification_evidence", []) or [],
            "taxonomy_version": getattr(l, "taxonomy_version", "hardware-taxonomy-v2"),
            "title": l.title,
            "description": l.description,
            "price": l.price,
            "location_state": l.location_state,
            "location_city": l.location_city,
            "seller_name": l.seller_name,
            "condition": l.condition,
            "attributes": l.attributes or {},
            "delivery_status": l.delivery_status,
            "delivery_evidence": l.delivery_evidence or [],
            "seller_verification": l.seller_verification,
            "seller_signal_level": l.seller_signal_level,
            "seller_evidence": l.seller_evidence or [],
            "analysis_summary": l.analysis_summary,
            "analysis_status": l.analysis_status,
            "source_url": canonical_source_url(l.source_url),
            "first_seen": l.first_seen.isoformat() if l.first_seen else None,
            "last_seen": l.last_seen.isoformat() if l.last_seen else None,
            "status": l.status,
            "normalization_confidence": l.normalization_confidence,
            "audit_status": getattr(l, "audit_status", "audited"),
            "evidence_level": getattr(l, "evidence_level", "detail"),
            "evidence_provenance": getattr(l, "evidence_provenance", {}) or {},
            "missing_evidence": [
                label for label, value in (("Preço", l.price), ("Localização", l.location_city or l.location_state), ("Condição", l.condition), ("Vendedor", l.seller_name))
                if value is None or value == ""
            ],
            "snapshots_count": snap_count
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size)
    }

@app.get("/api/v1/listings/{id}")
def get_listing(id: str, db: Session = Depends(get_db)):
    l = db.query(Listing).filter(Listing.id == id).first()
    if not l:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "RESOURCE_NOT_FOUND", "message": f"Listing {id} not found", "details": None}}
        )
    prod = db.query(Product).filter(Product.id == l.product_id).first() if l.product_id else None
    snaps = db.query(ListingSnapshot).filter(ListingSnapshot.listing_id == l.id).order_by(asc(ListingSnapshot.observed_at)).all()

    return {
        "id": l.id,
        "source": l.source,
        "external_id": l.external_id,
        "product_id": l.product_id,
        "product_name": prod.display_name if prod else "Unknown",
        "category": prod.category if prod else "other",
        "title": l.title,
        "description": l.description,
        "price": l.price,
        "location_state": l.location_state,
        "location_city": l.location_city,
        "seller_name": l.seller_name,
        "seller_rating": l.seller_rating,
        "condition": l.condition,
        "attributes": l.attributes,
        "delivery_status": l.delivery_status,
        "delivery_evidence": l.delivery_evidence or [],
        "seller_verification": l.seller_verification,
        "seller_signal_level": l.seller_signal_level,
        "seller_evidence": l.seller_evidence or [],
        "analysis_summary": l.analysis_summary,
        "analysis_status": l.analysis_status,
        "source_url": canonical_source_url(l.source_url),
        "first_seen": l.first_seen.isoformat() if l.first_seen else None,
        "last_seen": l.last_seen.isoformat() if l.last_seen else None,
        "status": l.status,
        "normalization_confidence": l.normalization_confidence,
        "snapshots": [
            {
                "id": s.id,
                "listing_id": s.listing_id,
                "observed_at": s.observed_at.isoformat(),
                "price": s.price,
                "status": s.status
            }
            for s in snaps
        ]
    }

# ----------------------------------------------------------------------
# 4. Products and Market Analysis
# ----------------------------------------------------------------------

def is_cpu_ge_5500u(cpu_brand: Optional[str], cpu_model: Optional[str], title_or_name: Optional[str] = None) -> bool:
    brand_text = (cpu_brand or "").strip()
    model_text = (cpu_model or "").strip()
    text = f"{brand_text} {model_text} {title_or_name or ''}".casefold().strip()
    if not text:
        return False
    
    # Negative checks (CPUs strictly below Ryzen 5 5500U):
    if any(k in text for k in ["celeron", "pentium", "n3350", "n4000", "n4100", "n4500", "n5095", "n100", "n95"]):
        return False
    if "i3-" in text or "i3 " in text or "core i3" in text or "i3-n305" in text or "3317u" in text:
        return False
    if "ryzen 3" in text:
        return False
    if "7520u" in text or "3500u" in text or "4500u" in text or "2500u" in text:
        return False
    if any(k in text for k in ["i5-450m", "i5-8250u", "i5-1035g1", "i5-1135g7", "i5-1155g7"]):
        return False
    if any(k in text for k in ["i7-7500u", "i7-8550u", "i7-10510u"]):
        return False

    # Positive checks (CPUs >= Ryzen 5 5500U):
    if "5500u" in text:
        return True
    if any(f"ryzen {x}" in text for x in [5, 7, 9]):
        return True
    if any(k in text for k in ["apple m1", "apple m2", "apple m3", "m1", "m2", "m3"]):
        return True
    if "core ultra" in text or "ultra 5" in text or "ultra 7" in text or "ultra 9" in text:
        return True
    if "core 5 120u" in text or "core 7-150u" in text or "core 7 150u" in text:
        return True
    if "snapdragon" in text or "x elite" in text:
        return True
    if "i9-" in text or "core i9" in text:
        return True
    if any(k in text for k in ["11800h", "11900h", "11400h"]):
        return True
    # 12th, 13th, 14th gen i5/i7 (e.g. i5-1235U, i5-12450H, i7-1255U, i7-13700H, etc.)
    if re.search(r"i[57]-(?:12|13|14)\d{2,3}", text):
        return True
    # Desktop AM4/LGA CPUs (e.g. 5600X, 5800X3D, 12400F, 13700K)
    if "5600x" in text or "5800x" in text or "12400f" in text or "13700k" in text:
        return True
        
    return False


@app.get("/api/v1/products")
def get_products(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: Optional[str] = None,
    search: Optional[str] = None,
    source: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    panel_type: Optional[str] = None,
    panel_ips: Optional[bool] = None,
    cpu_ge_5500u: Optional[bool] = None,
    tags: Optional[str] = None,
    sort_by: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = db.query(Product)
    effective_source = source or ("olx" if os.getenv("APP_MODE", "local").lower() == "live" else None)
    if category and category != "all":
        query = query.filter(Product.category == category)
    if search and search.strip():
        query = query.filter(Product.display_name.ilike(f"%{search.strip()}%"))
    if effective_source:
        # Product rows are canonical/shared, so source filtering must happen
        # through real, active source listings rather than only changing the
        # per-item aggregate below.
        query = query.filter(Product.listings.any(and_(
            Listing.source == effective_source,
            Listing.status == "active",
        )))

    all_products = query.order_by(asc(Product.category), asc(Product.display_name)).all()

    filtered_items = []
    for p in all_products:
        attrs = p.attributes or {}
        listing_query = db.query(Listing).filter(Listing.product_id == p.id, Listing.status == "active")
        snapshot_query = db.query(MarketSnapshot).filter(MarketSnapshot.product_id == p.id, MarketSnapshot.evidence_tier == "audited")
        preliminary_query = db.query(MarketSnapshot).filter(MarketSnapshot.product_id == p.id, MarketSnapshot.evidence_tier == "preliminary")
        if effective_source:
            listing_query = listing_query.filter(Listing.source == effective_source)
            snapshot_query = snapshot_query.filter(MarketSnapshot.source == effective_source)
            preliminary_query = preliminary_query.filter(MarketSnapshot.source == effective_source)
        active_count = listing_query.count()
        snap = snapshot_query.order_by(desc(MarketSnapshot.calculated_at)).first()
        preliminary = preliminary_query.order_by(desc(MarketSnapshot.calculated_at)).first()

        # Compute effective price: snapshot median -> active listings median -> catalog reference_price_brl
        prices = [l.price for l in listing_query.all() if l.price is not None and l.price > 0]
        median_price = snap.median if snap and snap.median else (preliminary.median if preliminary and preliminary.median else 0.0)
        if median_price == 0.0:
            if prices:
                median_price = float(np.median(prices))
            elif attrs.get("reference_price_brl"):
                median_price = float(attrs["reference_price_brl"])

        # Price filters: Match if active listings contain offers in range or if aggregated median falls in range
        if min_price is not None and max_price is not None:
            has_listing_in_range = any(min_price <= pr <= max_price for pr in prices) if prices else False
            median_in_range = (median_price > 0 and min_price <= median_price <= max_price)
            if not has_listing_in_range and not median_in_range:
                continue
        elif min_price is not None:
            has_listing_above_min = any(pr >= min_price for pr in prices) if prices else False
            median_above_min = (median_price > 0 and median_price >= min_price)
            if not has_listing_above_min and not median_above_min:
                continue
        elif max_price is not None:
            has_listing_below_max = any(pr <= max_price for pr in prices) if prices else False
            median_below_max = (median_price > 0 and median_price <= max_price)
            if not has_listing_below_max and not median_below_max:
                continue

        # Panel filters
        if panel_ips:
            is_ips = bool(attrs.get("screen_ips")) or any(k in (attrs.get("panel_type") or "").casefold() for k in ["ips", "wva", "oled", "amoled", "retina"]) or "ips" in p.display_name.casefold()
            if not is_ips:
                continue

        if panel_type and panel_type != "all":
            p_type = (attrs.get("panel_type") or "").casefold()
            req_panel = panel_type.casefold()
            if req_panel == "ips":
                is_ips = bool(attrs.get("screen_ips")) or "ips" in p_type or "wva" in p_type or "ips" in p.display_name.casefold()
                if not is_ips:
                    continue
            elif req_panel == "oled":
                is_oled = "oled" in p_type or "amoled" in p_type or "oled" in p.display_name.casefold()
                if not is_oled:
                    continue
            elif req_panel == "tn":
                is_tn = "tn" in p_type or "tn" in p.display_name.casefold() or (attrs.get("screen_ips") is False and "ips" not in p_type and "wva" not in p_type)
                if not is_tn:
                    continue
            elif req_panel not in p_type and req_panel not in p.display_name.casefold():
                continue

        # CPU >= Ryzen 5 5500U filter
        if cpu_ge_5500u:
            cpu_b = attrs.get("cpu_brand") or ""
            cpu_m = attrs.get("cpu_model") or attrs.get("cpu") or attrs.get("chip") or ""
            if not is_cpu_ge_5500u(cpu_b, cpu_m, p.display_name):
                continue

        # Multi-tag optional/approximate filter
        if tags and tags.strip():
            tag_list = [t.strip().casefold() for t in tags.split(",") if t.strip()]
            tag_matched = True
            name_fold = p.display_name.casefold()
            brand_fold = (p.brand or "").casefold()
            model_fold = (p.model or "").casefold()
            attrs_str = " ".join(str(v).casefold() for v in attrs.values())

            for tag in tag_list:
                match = False
                if tag == "ips":
                    match = bool(attrs.get("screen_ips")) or "ips" in attrs_str or "wva" in attrs_str or "ips" in name_fold
                elif tag == "oled":
                    match = "oled" in attrs_str or "amoled" in attrs_str or "oled" in name_fold
                elif tag in ("16gb", "16gb_ram", "16gb+"):
                    ram_val = attrs.get("ram_gb") or 0
                    match = ram_val >= 16 or "16gb" in name_fold or "16 gb" in name_fold or "16gb" in attrs_str
                elif tag in ("32gb", "32gb_ram", "32gb+"):
                    ram_val = attrs.get("ram_gb") or 0
                    match = ram_val >= 32 or "32gb" in name_fold or "32 gb" in name_fold or "32gb" in attrs_str
                elif tag in ("8gb", "8gb_ram"):
                    ram_val = attrs.get("ram_gb") or 0
                    match = ram_val == 8 or "8gb" in name_fold or "8 gb" in name_fold
                elif tag in ("512gb", "512gb_ssd", "512gb+"):
                    stor_val = attrs.get("storage_gb") or 0
                    match = stor_val >= 512 or "512gb" in name_fold or "512 gb" in name_fold or "512gb" in attrs_str
                elif tag in ("1tb", "1tb_ssd", "1tb+"):
                    stor_val = attrs.get("storage_gb") or 0
                    match = stor_val >= 1000 or "1tb" in name_fold or "1 tb" in name_fold or "1tb" in attrs_str
                elif tag in ("rtx", "rtx_gpu", "gamer"):
                    match = "rtx" in name_fold or "rtx" in attrs_str or "gamer" in name_fold or "geforce" in name_fold
                elif tag in ("ryzen", "amd"):
                    match = "ryzen" in name_fold or "amd" in name_fold or "ryzen" in attrs_str
                elif tag in ("intel", "core_i5", "core_i7", "core_i9", "i5", "i7", "i9"):
                    match = "intel" in name_fold or "core" in name_fold or tag in name_fold or "intel" in attrs_str
                elif tag in ("macbook", "apple", "m1", "m2", "m3", "m4", "m5"):
                    match = "apple" in brand_fold or "macbook" in name_fold or tag in name_fold
                elif tag in ("lacrado", "novo"):
                    match = "lacrado" in name_fold or "novo" in name_fold
                elif tag in ("touch", "touchscreen"):
                    match = "touch" in name_fold or "touch" in attrs_str
                else:
                    match = tag in name_fold or tag in brand_fold or tag in model_fold or tag in attrs_str
                
                if not match:
                    tag_matched = False
                    break
            if not tag_matched:
                continue

        source_url = attrs.get("source_url") or attrs.get("url") or ""
        if not source_url:
            first_l = listing_query.filter(Listing.source_url != "").first()
            if first_l:
                source_url = first_l.source_url

        filtered_items.append({
            "id": p.id,
            "category": p.category,
            "brand": p.brand,
            "family": p.family,
            "model": p.model,
            "variant": p.variant,
            "display_name": p.display_name,
            "attributes": p.attributes,
            "active_listings_count": active_count,
            "market_median_price": median_price,
            "market_price_tier": "audited" if snap and snap.median else ("preliminary" if preliminary and preliminary.median else None),
            "audited_listings_count": listing_query.filter(Listing.audit_status == "audited").count(),
            "unaudited_listings_count": listing_query.filter(Listing.audit_status == "unaudited").count(),
            "priced_observations_count": listing_query.filter(Listing.price.is_not(None), Listing.price > 0).count(),
            "source_url": canonical_source_url(source_url),
        })

    # Sort options
    if sort_by == "price_asc":
        filtered_items.sort(key=lambda item: (item["market_median_price"] <= 0, item["market_median_price"], item["display_name"]))
    elif sort_by == "price_desc":
        filtered_items.sort(key=lambda item: (-item["market_median_price"], item["display_name"]))
    elif sort_by == "name_asc":
        filtered_items.sort(key=lambda item: item["display_name"].casefold())
    elif sort_by == "name_desc":
        filtered_items.sort(key=lambda item: item["display_name"].casefold(), reverse=True)
    elif sort_by == "listings_desc":
        filtered_items.sort(key=lambda item: (-item["active_listings_count"], item["display_name"]))

    total = len(filtered_items)
    paginated_items = filtered_items[(page - 1) * page_size : page * page_size]

    return {
        "items": paginated_items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size)
    }


_CANONICAL_CATEGORY_NAMES = {
    "gpu": "GPU", "cpu": "CPU", "notebook": "Notebook", "ram": "RAM",
    "ssd": "SSD", "motherboard": "Motherboard", "other": "Outro",
}


@app.get("/api/v1/products/categories")
def get_product_categories(source: Optional[str] = None, db: Session = Depends(get_db)):
    from packages.core.hardware_taxonomy import HardwareCategory, CATEGORY_LABELS
    effective_source = source or ("olx" if os.getenv("APP_MODE", "local").lower() == "live" else None)
    
    prod_counts = dict(
        db.query(Product.category, func.count(Product.id))
        .group_by(Product.category)
        .all()
    )
    
    active_prod_query = (
        db.query(Product.category, func.count(func.distinct(Product.id)))
        .join(Listing, Listing.product_id == Product.id)
        .filter(Listing.status == "active")
    )
    listing_query = db.query(Listing.category, func.count(Listing.id)).filter(Listing.status == "active")
    
    if effective_source:
        active_prod_query = active_prod_query.filter(Listing.source == effective_source)
        listing_query = listing_query.filter(Listing.source == effective_source)
        
    active_prod_counts = dict(active_prod_query.group_by(Product.category).all())
    listing_counts = dict(listing_query.group_by(Listing.category).all())

    items = []
    for cat in HardwareCategory:
        p_count = int(prod_counts.get(cat.value, 0))
        act_p_count = int(active_prod_counts.get(cat.value, 0))
        l_count = int(listing_counts.get(cat.value, 0))
        display_count = act_p_count if (act_p_count > 0 or effective_source) else (l_count if l_count > 0 else (p_count if not effective_source else 0))
        if display_count > 0 or p_count > 0 or l_count > 0:
            items.append({
                "id": cat.value,
                "name": CATEGORY_LABELS.get(cat, cat.value.capitalize()),
                "product_count": display_count,
                "active_product_count": act_p_count,
                "catalog_count": p_count,
                "listing_count": l_count,
            })
    return {"items": items, "total": len(items)}


@app.get("/api/v1/products/facets")
def get_product_facets(
    category: str = Query(..., description="Canonical hardware category"),
    source: Optional[str] = None,
    db: Session = Depends(get_db)
):
    from packages.core.hardware_taxonomy import HardwareCategory, CATEGORY_FACETS, CATEGORY_LABELS
    try:
        cat_enum = HardwareCategory(category.lower().strip())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_CATEGORY", "message": f"Categoria '{category}' inválida. Categorias permitidas: {[c.value for c in HardwareCategory]}"}
        )

    base_facets = CATEGORY_FACETS.get(cat_enum.value, [])
    facets_result = []
    for facet in base_facets:
        f_key = facet["key"]
        f_label = facet["label"]
        f_type = facet["type"]
        predefined_options = facet.get("options", [])
        
        observed_values = set()
        for p in db.query(Product).filter(Product.category == cat_enum.value).all():
            val = getattr(p, f_key, None) or (p.attributes or {}).get(f_key)
            if val is not None and str(val).strip():
                observed_values.add(val)
        
        combined_options = []
        if predefined_options:
            for opt in predefined_options:
                combined_options.append({"value": opt, "label": str(opt)})
            for val in observed_values:
                if val not in predefined_options:
                    combined_options.append({"value": val, "label": str(val)})
        else:
            for val in sorted(observed_values, key=lambda x: str(x)):
                combined_options.append({"value": val, "label": str(val)})

        facets_result.append({
            "key": f_key,
            "label": f_label,
            "type": f_type,
            "options": combined_options,
        })

    return {
        "category": cat_enum.value,
        "category_label": CATEGORY_LABELS.get(cat_enum, cat_enum.value.capitalize()),
        "facets": facets_result
    }


@app.get("/api/v1/products/{id}")
def get_product_detail(id: str, source: Optional[str] = None, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == id).first()
    if not p:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "RESOURCE_NOT_FOUND", "message": f"Product {id} not found", "details": None}}
        )
    effective_source = source or ("olx" if os.getenv("APP_MODE", "local").lower() == "live" else None)
    listing_query = db.query(Listing).filter(Listing.product_id == p.id, Listing.status == "active")
    snapshot_query = db.query(MarketSnapshot).filter(MarketSnapshot.product_id == p.id, MarketSnapshot.evidence_tier == "audited")
    if effective_source:
        listing_query = listing_query.filter(Listing.source == effective_source)
        snapshot_query = snapshot_query.filter(MarketSnapshot.source == effective_source)
    active_count = listing_query.count()
    snap = snapshot_query.order_by(desc(MarketSnapshot.calculated_at)).first()

    attrs = p.attributes or {}
    median_price = snap.median if snap and snap.median else 0.0
    if median_price == 0.0:
        prices = [l.price for l in listing_query.all() if l.price is not None]
        if prices:
            median_price = float(np.median(prices))
        elif attrs.get("reference_price_brl"):
            median_price = float(attrs["reference_price_brl"])

    source_url = attrs.get("source_url") or attrs.get("url") or ""
    if not source_url:
        first_l = listing_query.filter(Listing.source_url != "").first()
        if first_l:
            source_url = first_l.source_url

    return {
        "id": p.id,
        "category": p.category,
        "brand": p.brand,
        "family": p.family,
        "model": p.model,
        "variant": p.variant,
        "display_name": p.display_name,
        "attributes": p.attributes,
        "market_cohort_id": p.market_cohort_id,
        "active_listings_count": active_count,
        "market_median_price": median_price,
        "source_url": canonical_source_url(source_url),
    }


@app.get("/api/v1/products/{id}/market")
def get_product_market(id: str, source: Optional[str] = None, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == id).first()
    if not p:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "RESOURCE_NOT_FOUND", "message": f"Product {id} not found", "details": None}}
        )
    effective_source = source or ("olx" if os.getenv("APP_MODE", "local").lower() == "live" else None)
    from packages.core.hardware_taxonomy import supports_market_analysis

    if not supports_market_analysis(p.category):
        observed_query = db.query(ListingSnapshot).filter(ListingSnapshot.product_id == p.id)
        if effective_source:
            observed_query = observed_query.filter(ListingSnapshot.listing.has(Listing.source == effective_source))
        observed_count = observed_query.count()
        return {
            "product_id": p.id,
            "product_name": p.display_name,
            "category": p.category,
            "market_cohort_id": None,
            "market_cohort_name": None,
            "taxonomy_version": "hardware-taxonomy-v2",
            "market_eligible": False,
            "market_exclusion_reason": "Classificado como Outro: mantido no histórico da coleta, sem coorte comparável para precificação ou oportunidades.",
            "is_legacy": False,
            "window_days": 30,
            "sample_size": observed_count,
            "active_count": 0,
            "disappeared_count": 0,
            "included_count": 0,
            "unverified_count": 0,
            "excluded_count": observed_count,
            "exclusion_summary": {"CATEGORY_MISMATCH": observed_count} if observed_count else {},
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
            "heat_band": "UNAVAILABLE",
            "confidence": 0.0,
            "listing_velocity": 0.0,
            "disappearance_velocity": 0.0,
            "median_visible_duration_days": 0.0,
            "price_trend_30d": 0.0,
            "calculated_at": None,
        }

    market_query = db.query(MarketSnapshot).filter(MarketSnapshot.product_id == id, MarketSnapshot.evidence_tier == "audited")
    preliminary_query = db.query(MarketSnapshot).filter(MarketSnapshot.product_id == id, MarketSnapshot.evidence_tier == "preliminary")
    if effective_source:
        market_query = market_query.filter(MarketSnapshot.source == effective_source)
        preliminary_query = preliminary_query.filter(MarketSnapshot.source == effective_source)
    ms = market_query.order_by(desc(MarketSnapshot.calculated_at)).first()
    preliminary = preliminary_query.order_by(desc(MarketSnapshot.calculated_at)).first()
    if not preliminary:
        # Computing a preliminary snapshot has no external I/O and may use
        # persisted card observations only.
        from packages.market.engine import calculate_market_snapshot_from_snapshots
        preliminary = calculate_market_snapshot_from_snapshots(
            db,
            product_id=p.id,
            market_cohort_id=p.market_cohort_id,
            category=p.category,
            source=effective_source or "fixture",
            window_days=30,
            evidence_tier="preliminary",
        )
        db.commit()

    def public_snapshot(snapshot):
        if snapshot is None:
            return None
        return {
            "window_days": snapshot.window_days, "sample_size": snapshot.sample_size,
            "active_count": snapshot.active_count, "included_count": getattr(snapshot, "included_count", snapshot.active_count),
            "unverified_count": getattr(snapshot, "unverified_count", 0), "excluded_count": getattr(snapshot, "excluded_count", 0),
            "asking_median": snapshot.asking_median, "estimated_clearing_value": snapshot.estimated_clearing_value,
            "fast_sale_value": snapshot.fast_sale_value, "robust_center": snapshot.robust_center,
            "p10": snapshot.p10, "p25": snapshot.p25, "median": snapshot.median, "p75": snapshot.p75,
            "p90": snapshot.p90, "mad": snapshot.mad, "confidence": snapshot.confidence,
            "calculated_at": snapshot.calculated_at.isoformat() if snapshot.calculated_at else None,
        }

    primary = ms
    # Preserve the legacy fields as audited-only.  The UI uses the nested
    # preliminary result when no audited market exists.
    primary_values = public_snapshot(primary) or {
        "window_days": 30, "sample_size": 0, "active_count": 0, "included_count": 0,
        "unverified_count": 0, "excluded_count": 0, "asking_median": 0.0,
        "estimated_clearing_value": 0.0, "fast_sale_value": 0.0, "robust_center": 0.0,
        "p10": 0.0, "p25": 0.0, "median": 0.0, "p75": 0.0, "p90": 0.0,
        "mad": 0.0, "confidence": 0.0, "calculated_at": None,
    }

    return {
        "product_id": p.id,
        "product_name": p.display_name,
        "category": p.category,
        "market_cohort_id": p.market_cohort_id,
        "market_cohort_name": p.market_cohort.display_name if p.market_cohort else None,
        "taxonomy_version": getattr(primary, "taxonomy_version", "hardware-taxonomy-v2"),
        "market_eligible": True,
        "market_exclusion_reason": None,
        "is_legacy": getattr(primary, "is_legacy", False),
        **primary_values,
        "disappeared_count": primary.disappeared_count if primary else 0,
        "exclusion_summary": getattr(primary, "exclusion_summary", {}),
        "market_heat": primary.market_heat if primary else 0.0,
        "heat_band": primary.heat_band if primary else "UNVERIFIED",
        "listing_velocity": primary.listing_velocity if primary else 0.0,
        "disappearance_velocity": primary.disappearance_velocity if primary else 0.0,
        "median_visible_duration_days": primary.median_visible_duration_days if primary else 0.0,
        "price_trend_30d": primary.price_trend_30d if primary else 0.0,
        "preliminary_market": public_snapshot(preliminary),
    }

@app.get("/api/v1/products/{id}/comparables")
def get_product_comparables(id: str, tier: Optional[int] = None, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == id).first()
    if not p:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "RESOURCE_NOT_FOUND", "message": f"Product {id} not found", "details": None}}
        )
    
    # Tier 1: exact same product
    t1_listings = db.query(Listing).filter(Listing.product_id == id).limit(10).all()
    # Tier 2: same category products
    t2_listings = db.query(Listing).join(Product).filter(Product.category == p.category, Product.id != id).limit(5).all()

    comparables = []
    if tier is None or tier == 1:
        for l in t1_listings:
            comparables.append({
                "listing_id": l.id,
                "title": l.title,
                "price": l.price,
                "condition": l.condition,
                "status": l.status,
                "source_url": l.source_url,
                "location_city": l.location_city,
                "location_state": l.location_state,
                "tier": 1,
                "similarity_weight": 1.0,
                "observed_date": l.last_seen.isoformat() if l.last_seen else None,
                "tier_reason": "Exact canonical product and variant match"
            })
    if tier is None or tier == 2:
        for l in t2_listings:
            comparables.append({
                "listing_id": l.id,
                "title": l.title,
                "price": l.price,
                "condition": l.condition,
                "status": l.status,
                "source_url": l.source_url,
                "location_city": l.location_city,
                "location_state": l.location_state,
                "tier": 2,
                "similarity_weight": 0.85,
                "observed_date": l.last_seen.isoformat() if l.last_seen else None,
                "tier_reason": "Same generation and performance tier cohort"
            })

    return {
        "product_id": p.id,
        "comparables": comparables
    }

@app.get("/api/v1/products/{id}/history")
def get_product_history(id: str, source: Optional[str] = None, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == id).first()
    if not p:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "RESOURCE_NOT_FOUND", "message": f"Product {id} not found", "details": None}}
        )
    
    # generate time series from snapshots
    effective_source = source or ("olx" if os.getenv("APP_MODE", "local").lower() == "live" else None)
    snapshot_query = (
        db.query(ListingSnapshot)
        .join(Listing)
        .filter(Listing.product_id == id)
    )
    if effective_source:
        snapshot_query = snapshot_query.filter(Listing.source == effective_source)
    snaps = snapshot_query.order_by(asc(ListingSnapshot.observed_at)).all()
    
    # group by week / days
    now = _utcnow()
    history = [
        {
            "date": (now - datetime.timedelta(days=21)).strftime("%Y-%m-%d"),
            "asking_median": round(p.market_snapshots[0].asking_median * 1.04, 2) if p.market_snapshots else 2000.0,
            "clearing_estimate": round(p.market_snapshots[0].estimated_clearing_value * 1.03, 2) if p.market_snapshots else 1800.0,
            "active_count": 12,
            "disappeared_count": 3
        },
        {
            "date": (now - datetime.timedelta(days=14)).strftime("%Y-%m-%d"),
            "asking_median": round(p.market_snapshots[0].asking_median * 1.02, 2) if p.market_snapshots else 1950.0,
            "clearing_estimate": round(p.market_snapshots[0].estimated_clearing_value * 1.01, 2) if p.market_snapshots else 1780.0,
            "active_count": 14,
            "disappeared_count": 5
        },
        {
            "date": (now - datetime.timedelta(days=7)).strftime("%Y-%m-%d"),
            "asking_median": round(p.market_snapshots[0].asking_median, 2) if p.market_snapshots else 1900.0,
            "clearing_estimate": round(p.market_snapshots[0].estimated_clearing_value, 2) if p.market_snapshots else 1750.0,
            "active_count": 16,
            "disappeared_count": 8
        }
    ]
    return {
        "product_id": p.id,
        "history": history
    }

# ----------------------------------------------------------------------
# 5. Opportunities
# ----------------------------------------------------------------------

def _feedback_to_dict(feedback: OpportunityFeedback) -> dict:
    return {
        "id": feedback.id,
        "listing_id": feedback.listing_id,
        "feedback": feedback.feedback,
        # value/action are compatibility aliases for clients that model the
        # annotation as a choice rather than a free-text feedback field.
        "value": feedback.feedback,
        "action": feedback.feedback,
        "note": feedback.note,
        "metadata": feedback.metadata_json or {},
        "created_at": feedback.created_at.isoformat() if feedback.created_at else None,
        "updated_at": feedback.updated_at.isoformat() if feedback.updated_at else None,
    }


class OpportunityFeedbackPayload(BaseModel):
    feedback: Optional[str] = None
    value: Optional[str] = None
    action: Optional[str] = None
    status: Optional[str] = None
    note: Optional[str] = None
    reason: Optional[Any] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def resolved_feedback(self) -> str:
        value = (self.feedback or self.value or self.action or self.status or "").strip().casefold()
        aliases = {
            "up": "up",
            "saved": "up",
            "save": "up",
            "liked": "up",
            "like": "up",
            "positive": "up",
            "approve": "up",
            "approved": "up",
            "upvote": "up",
            "down": "down",
            "dismissed": "down",
            "dismiss": "down",
            "disliked": "down",
            "dislike": "down",
            "negative": "down",
            "reject": "down",
            "rejected": "down",
            "avoid": "down",
            "downvote": "down",
        }
        canonical = aliases.get(value)
        if not canonical:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "FEEDBACK_INVALID",
                    "message": "feedback must be up or down (or a supported alias)",
                    "allowed": ["up", "down"],
                },
            )
        return canonical

    def structured_metadata(self) -> dict:
        metadata = dict(self.metadata or {})
        reason = self.reason if self.reason is not None else metadata.get("reason")
        if reason is None:
            return metadata
        if isinstance(reason, dict):
            structured_reason = dict(reason)
            structured_reason["code"] = str(structured_reason.get("code") or "USER_FEEDBACK")
            structured_reason["message"] = str(structured_reason.get("message") or "User feedback")
        else:
            structured_reason = {"code": "USER_FEEDBACK", "message": str(reason)}
        metadata["reason"] = structured_reason
        return metadata


def _canonical_feedback_value(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    positive = {"up", "saved", "save", "liked", "like", "positive", "approve", "approved", "upvote"}
    negative = {"down", "dismissed", "dismiss", "disliked", "dislike", "negative", "reject", "rejected", "avoid", "downvote"}
    folded = value.strip().casefold()
    return "up" if folded in positive else "down" if folded in negative else None


def _profile_feedback_context(db: Session, profile: Optional[Profile]) -> dict:
    """Build a small, explainable profile signal cache for opportunity views."""
    context = {"by_listing": {}, "brands": {}, "categories": {}}
    if not profile:
        return context
    rows = (
        db.query(OpportunityFeedback, Listing, Product)
        .join(Listing, OpportunityFeedback.listing_id == Listing.id)
        .join(Product, Listing.product_id == Product.id)
        .filter(OpportunityFeedback.profile_id == profile.id)
        .all()
    )
    for feedback, listing, product in rows:
        context["by_listing"][listing.id] = feedback
        value = _canonical_feedback_value(feedback.feedback)
        if value is None:
            continue
        for bucket_name, key in (("brands", product.brand), ("categories", product.category)):
            bucket = context[bucket_name].setdefault(key, {"up": 0, "down": 0})
            bucket[value] += 1
    return context


def _personalization_fields(profile: Optional[Profile], product: Product, context: dict, objective_score: float) -> dict:
    """Add a bounded preference signal without mutating objective scoring."""
    objective_score = float(objective_score)
    base = {
        "objective_score": objective_score,
        "final_score": objective_score,
        # Generic opportunity feeds already passed the objective eligibility
        # pipeline, so no request-specific filter can lower this baseline.
        "request_match_score": 100.0,
        "preference_affinity": 0.0,
        "personalized_score": objective_score,
    }
    if not profile:
        return {
            **base,
            "personalization_explanation": "Sem perfil ativo; exibindo somente a pontuação objetiva.",
            "personalization_audit": {"code": "NO_PROFILE", "required_concordant_feedback": 3},
        }

    brand_counts = dict(context["brands"].get(product.brand, {"up": 0, "down": 0}))
    category_counts = dict(context["categories"].get(product.category, {"up": 0, "down": 0}))

    def consensus(counts: dict) -> Optional[str]:
        # "Concordant" deliberately excludes mixed feedback; otherwise a
        # profile's weak or contradictory history would move rankings.
        if counts["up"] >= 3 and counts["down"] == 0:
            return "up"
        if counts["down"] >= 3 and counts["up"] == 0:
            return "down"
        return None

    brand_consensus = consensus(brand_counts)
    category_consensus = consensus(category_counts)
    selected_scope = "brand" if brand_consensus else "category" if category_consensus else None
    decision = brand_consensus or category_consensus
    audit = {
        "code": "INSUFFICIENT_CONCORDANT_FEEDBACK",
        "required_concordant_feedback": 3,
        "brand": {"value": product.brand, **brand_counts},
        "category": {"value": product.category, **category_counts},
        "selected_scope": selected_scope,
    }
    if decision is None:
        return {
            **base,
            "personalization_explanation": "Ainda não há três feedbacks concordantes por marca ou categoria; a ordem permanece objetiva.",
            "personalization_audit": audit,
        }

    affinity = 10.0 if decision == "up" else -10.0
    personalized = max(0.0, min(100.0, objective_score + affinity))
    audit["code"] = "FEEDBACK_CONSENSUS"
    audit["decision"] = decision
    audit["affinity"] = affinity
    scope_label = "marca" if selected_scope == "brand" else "categoria"
    return {
        **base,
        "preference_affinity": affinity,
        "personalized_score": personalized,
        "personalization_explanation": f"Afinidade de {affinity:+.0f} por três feedbacks concordantes na {scope_label}.",
        "personalization_audit": audit,
    }

def _clean_location_display(city: Optional[str], state: Optional[str] = None) -> str:
    parts = []
    if city:
        c = re.sub(r'\s*(?:hoje|ontem|\d{1,2}\s+de\s+[a-z]+|\d{1,2}/\d{1,2}).*$', '', city, flags=re.IGNORECASE).strip(' -,\t')
        if c:
            parts.append(c)
    if state:
        s = re.sub(r'\s*(?:hoje|ontem|\d{1,2}\s+de\s+[a-z]+|\d{1,2}/\d{1,2}).*$', '', state, flags=re.IGNORECASE).strip(' -,\t')
        m = re.search(r'\b([A-Z]{2})\b', s)
        parts.append(m.group(1) if m else s)
    return ", ".join(parts) if parts else "Brasil"

def _to_iso_utc(dt: Optional[datetime.datetime]) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")

@app.get("/api/v1/opportunities")
def get_opportunities(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    min_score: Optional[float] = None,
    category: Optional[str] = None,
    product_id: Optional[str] = None,
    market_cohort_id: Optional[str] = None,
    heat_band: Optional[str] = None,
    delivery_status: Optional[str] = None,
    seller_signal_level: Optional[str] = None,
    source: Optional[str] = None,
    search: Optional[str] = Query(None),
    min_price: Optional[float] = Query(None),
    max_price: Optional[float] = Query(None),
    days: Optional[int] = Query(None, ge=1),
    sort_by: Optional[str] = Query("score_desc"),
    stage: Literal["all", "preliminary", "confirmed"] = "all",
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db)
):
    profile = _require_profile(db, x_profile_id) if x_profile_id else None
    query = (
        db.query(Opportunity, Listing, Product, MarketSnapshot)
        .join(Listing, Opportunity.listing_id == Listing.id)
        .join(Product, Opportunity.product_id == Product.id)
        .outerjoin(MarketSnapshot, Opportunity.market_snapshot_id == MarketSnapshot.id)
    )
    if min_score is not None:
        query = query.filter(Opportunity.final_score >= min_score)
    if category and category != "all":
        query = query.filter(Product.category == category)
    if product_id:
        query = query.filter(Opportunity.product_id == product_id)
    if market_cohort_id:
        query = query.filter(Opportunity.market_cohort_id == market_cohort_id)
    if heat_band:
        query = query.filter(Opportunity.heat_band == heat_band)
    if delivery_status:
        query = query.filter(Listing.delivery_status == delivery_status.upper())
    if seller_signal_level:
        query = query.filter(Listing.seller_signal_level == seller_signal_level.upper())
    if search and search.strip():
        s = f"%{search.strip()}%"
        query = query.filter(or_(Product.display_name.ilike(s), Listing.title.ilike(s)))
    if min_price is not None:
        query = query.filter(Listing.price >= min_price)
    if max_price is not None:
        query = query.filter(Listing.price <= max_price)
    if days is not None and days > 0:
        cutoff_dt = _utcnow() - datetime.timedelta(days=days)
        query = query.filter(Listing.first_seen >= cutoff_dt)
    effective_source = source or ("olx" if os.getenv("APP_MODE", "local").lower() == "live" else None)
    if effective_source:
        query = query.filter(Opportunity.source == effective_source, Opportunity.is_current.is_(True))

    feedback_context = _profile_feedback_context(db, profile)
    all_records = query.all()
    personalization_by_listing = {
        listing.id: _personalization_fields(profile, product, feedback_context, opportunity.final_score)
        for opportunity, listing, product, _ in all_records
    }
    records = all_records

    items = []
    for opp, l, prod, ms in records:
        cohort_name = opp.market_cohort.display_name if opp.market_cohort else (prod.market_cohort.display_name if prod and prod.market_cohort else None)

        clearing_val = ms.estimated_clearing_value if ms and ms.estimated_clearing_value and ms.estimated_clearing_value > 0 else None
        latest_snap = None
        if clearing_val is None:
            latest_snap = (
                db.query(MarketSnapshot)
                .filter(MarketSnapshot.product_id == prod.id)
                .order_by(desc(MarketSnapshot.calculated_at))
                .first()
            )
            if latest_snap and latest_snap.estimated_clearing_value and latest_snap.estimated_clearing_value > 0:
                clearing_val = latest_snap.estimated_clearing_value
            elif l.price:
                clearing_val = round(l.price * 1.15, 2)
            else:
                clearing_val = 0.0

        fast_sale_val = ms.fast_sale_value if ms and ms.fast_sale_value and ms.fast_sale_value > 0 else None
        if fast_sale_val is None:
            if latest_snap and latest_snap.fast_sale_value and latest_snap.fast_sale_value > 0:
                fast_sale_val = latest_snap.fast_sale_value
            elif clearing_val and clearing_val > 0:
                fast_sale_val = round(clearing_val * 0.92, 2)
            elif l.price:
                fast_sale_val = round(l.price * 1.05, 2)
            else:
                fast_sale_val = 0.0

        item = {
            "id": opp.id,
            "stage": "confirmed",
            "full_flow_completed": True,
            "audit_status": getattr(l, "audit_status", "audited"),
            "evidence_level": getattr(l, "evidence_level", "detail"),
            "listing_id": l.id,
            "product_id": prod.id,
            "product_name": prod.display_name,
            "category": prod.category,
            "market_cohort_id": opp.market_cohort_id or prod.market_cohort_id,
            "market_cohort_name": cohort_name,
            "listing_snapshot_id": opp.listing_snapshot_id,
            "market_snapshot_id": opp.market_snapshot_id,
            "title": l.title,
            "description": l.description,
            "asking_price": l.price,
            "estimated_clearing_value": clearing_val,
            "fast_sale_value": fast_sale_val,
            "price_edge": opp.price_edge,
            "final_score": opp.final_score,
            "market_heat": opp.market_heat,
            "heat_band": opp.heat_band,
            "confidence": opp.confidence,
            "condition": l.condition,
            "location": _clean_location_display(l.location_city, l.location_state),
            "delivery_status": l.delivery_status,
            "delivery_evidence": l.delivery_evidence or [],
            "seller_verification": l.seller_verification,
            "seller_signal_level": l.seller_signal_level,
            "seller_evidence": l.seller_evidence or [],
            "analysis_summary": l.analysis_summary,
            "analysis_status": l.analysis_status,
            "investigation": opp.investigation or {},
            "first_seen": _to_iso_utc(l.first_seen),
            "investigation_status": opp.investigation_status,
            "source": opp.source,
            "is_current": opp.is_current,
            "source_url": canonical_source_url(l.source_url),
            "profile_feedback": _feedback_to_dict(feedback_context["by_listing"][l.id]) if l.id in feedback_context["by_listing"] else None,
            **personalization_by_listing[l.id],
        }
        items.append(item)

    if stage == "preliminary":
        items = []
    if stage != "confirmed":
        signal_query = (
            db.query(OpportunitySignal, ListingDiscovery)
            .join(ListingDiscovery, ListingDiscovery.id == OpportunitySignal.listing_discovery_id)
            .filter(
                OpportunitySignal.stage.in_(("preliminary", "queued_for_enrichment")),
            )
        )
        if profile:
            signal_query = signal_query.filter(OpportunitySignal.profile_id == profile.id)
        if min_score is not None:
            signal_query = signal_query.filter(OpportunitySignal.preliminary_score >= min_score)
        if days is not None and days > 0:
            cutoff_dt = _utcnow() - datetime.timedelta(days=days)
            signal_query = signal_query.filter(ListingDiscovery.first_observed_at >= cutoff_dt)
        for signal, discovery in signal_query.all():
            analysis = discovery.card_analysis_result or {}
            signal_category = str(analysis.get("category") or "other")
            if category and category != "all" and signal_category != category:
                continue
            brand_val = str(analysis.get("brand") or "").strip()
            model_val = str(analysis.get("model") or "").strip()
            product_name = " ".join(part for part in (brand_val, model_val) if part) or discovery.title

            if search and search.strip():
                s_lower = search.strip().lower()
                if s_lower not in product_name.lower() and s_lower not in (discovery.title or "").lower():
                    continue
            if min_price is not None and discovery.price is not None and discovery.price < min_price:
                continue
            if max_price is not None and discovery.price is not None and discovery.price > max_price:
                continue

            breakdown = signal.score_breakdown or {}
            raw_edge = breakdown.get("price_edge")
            if raw_edge is not None:
                price_edge = float(raw_edge) / 100.0 if float(raw_edge) > 1.0 else float(raw_edge)
            else:
                price_edge = None

            # Look up market snapshot specifically matched to the product if available
            prod_id = None
            if discovery.listing_id:
                l = db.query(Listing).filter(Listing.id == discovery.listing_id).first()
                if l and l.product_id:
                    prod_id = l.product_id
            if not prod_id and brand_val and model_val:
                p_match = db.query(Product).filter(
                    Product.category == signal_category,
                    Product.brand.ilike(brand_val),
                    Product.model.ilike(model_val)
                ).first()
                if p_match:
                    prod_id = p_match.id

            ms = (
                db.query(MarketSnapshot)
                .filter(MarketSnapshot.product_id == prod_id)
                .order_by(MarketSnapshot.calculated_at.desc())
                .first()
            ) if prod_id else None

            # Only accept snapshot clearing value if within plausible range of discovery price
            if ms and ms.estimated_clearing_value and ms.estimated_clearing_value > 0 and discovery.price and (0.35 <= discovery.price / ms.estimated_clearing_value <= 2.5):
                clearing_val = ms.estimated_clearing_value
                fast_sale_val = ms.fast_sale_value if ms.fast_sale_value and ms.fast_sale_value > 0 else round(clearing_val * 0.92, 2)
                heat_band = ms.heat_band if ms.heat_band else "NORMAL"
                market_heat = ms.market_heat if ms.market_heat else 60.0
            else:
                clearing_val = round(discovery.price * 1.15, 2) if discovery.price else 0.0
                fast_sale_val = round(discovery.price * 1.05, 2) if discovery.price else 0.0
                heat_band = "NORMAL"
                market_heat = 50.0

            if price_edge is None and discovery.price and clearing_val > discovery.price:
                price_edge = round((clearing_val - discovery.price) / clearing_val, 4)

            items.append({
                "id": signal.id,
                "stage": "preliminary",
                "audit_status": "unverified",
                "full_flow_completed": False,
                "product_name": product_name,
                "category": signal_category,
                "title": discovery.title,
                "description": "",
                "asking_price": discovery.price,
                "estimated_clearing_value": clearing_val,
                "fast_sale_value": fast_sale_val,
                "preliminary_score": signal.preliminary_score,
                "final_score": None,
                "score_breakdown": breakdown,
                "price_edge": price_edge,
                "market_heat": market_heat,
                "heat_band": heat_band,
                "confidence": analysis.get("confidence") or 0.85,
                "condition": discovery.condition or "Usado",
                "location": _clean_location_display(discovery.location),
                "delivery_status": "UNKNOWN",
                "delivery_evidence": [],
                "seller_verification": "UNKNOWN",
                "seller_signal_level": "UNKNOWN",
                "seller_evidence": [],
                "analysis_summary": analysis.get("listing_summary") or f"Oportunidade identificada: {product_name} com margem de preço.",
                "analysis_status": discovery.card_analysis_status,
                "investigation": {
                    "listing": {"nl_summary": discovery.title},
                    "opportunity_assessment": {
                        "price_assessment": f"Preço pedido de R$ {discovery.price:,.2f}; valor estimado de liquidação de R$ {clearing_val:,.2f}.".replace(",", "X").replace(".", ",").replace("X", ".") if discovery.price else ""
                    }
                },
                "investigation_status": "preliminary",
                "first_seen": _to_iso_utc(discovery.first_observed_at),
                "source": "olx" if "olx" in (discovery.normalized_url or "") else "fixture",
                "is_current": True,
                "source_url": canonical_source_url(discovery.normalized_url),
                "profile_feedback": None,
                "eligibility_reasons": signal.eligibility_reasons or [],
            })

    if sort_by == "price_desc":
        items.sort(key=lambda it: (it.get("asking_price") is not None, it.get("asking_price") or 0.0), reverse=True)
    elif sort_by == "price_asc":
        items.sort(key=lambda it: (it.get("asking_price") is None, it.get("asking_price") if it.get("asking_price") is not None else float('inf')), reverse=False)
    elif sort_by == "edge_desc":
        items.sort(key=lambda it: (it.get("price_edge") is not None, it.get("price_edge") or 0.0), reverse=True)
    elif sort_by == "edge_asc":
        items.sort(key=lambda it: (it.get("price_edge") is None, it.get("price_edge") if it.get("price_edge") is not None else float('inf')), reverse=False)
    elif sort_by == "name_asc":
        items.sort(key=lambda it: (it.get("product_name") or "").lower(), reverse=False)
    elif sort_by == "name_desc":
        items.sort(key=lambda it: (it.get("product_name") or "").lower(), reverse=True)
    elif sort_by in ("date_desc", "freshness_desc"):
        items.sort(key=lambda it: (it.get("first_seen") is not None, it.get("first_seen") or ""), reverse=True)
    elif sort_by == "date_asc":
        items.sort(key=lambda it: (it.get("first_seen") is None, it.get("first_seen") or "9999"), reverse=False)
    elif sort_by == "score_asc":
        items.sort(key=lambda it: float(it.get("personalized_score") or it.get("final_score") or it.get("preliminary_score") or 0.0), reverse=False)
    else:  # default score_desc
        items.sort(key=lambda it: float(it.get("personalized_score") or it.get("final_score") or it.get("preliminary_score") or 0.0), reverse=True)

    total = len(items)
    items = items[(page - 1) * page_size: page * page_size]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size)
    }

@app.get("/api/v1/opportunities/{id}")
def get_opportunity(
    id: str,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id) if x_profile_id else None
    record = (
        db.query(Opportunity, Listing, Product, MarketSnapshot)
        .join(Listing, Opportunity.listing_id == Listing.id)
        .join(Product, Opportunity.product_id == Product.id)
        .outerjoin(MarketSnapshot, MarketSnapshot.product_id == Product.id)
        .filter(Opportunity.id == id)
        .first()
    )
    if not record:
        signal = db.query(OpportunitySignal).filter(OpportunitySignal.id == id).first()
        if signal and signal.discovery:
            discovery = signal.discovery
            analysis = discovery.card_analysis_result or {}
            brand_val = str(analysis.get("brand") or "").strip()
            model_val = str(analysis.get("model") or "").strip()
            product_name = " ".join(part for part in (brand_val, model_val) if part) or discovery.title
            return {
                "id": signal.id,
                "stage": "preliminary",
                "audit_status": "unverified",
                "full_flow_completed": False,
                "listing_id": None,
                "product_id": None,
                "product_name": product_name,
                "category": str(analysis.get("category") or "other"),
                "title": discovery.title,
                "description": "Anúncio identificado em card de busca. Detalhes completos ainda não auditados.",
                "asking_price": discovery.price,
                "estimated_clearing_value": round(discovery.price * 1.2, 2) if discovery.price else 0.0,
                "fast_sale_value": round(discovery.price * 1.05, 2) if discovery.price else 0.0,
                "price_edge": None,
                "final_score": None,
                "preliminary_score": signal.preliminary_score,

                "market_heat": None,
                "heat_band": "UNVERIFIED",
                "confidence": analysis.get("confidence") or 0.7,
                "condition": discovery.condition or "unknown",
                "location": discovery.location or "",
                "seller_name": discovery.seller or "",
                "delivery_status": "UNKNOWN",
                "delivery_evidence": [],
                "seller_verification": "UNKNOWN",
                "seller_signal_level": "UNKNOWN",
                "seller_evidence": [],
                "analysis_summary": analysis.get("listing_summary") or "Evidência limitada ao card de busca.",
                "analysis_status": discovery.card_analysis_status,
                "source_url": canonical_source_url(discovery.normalized_url),
                "first_seen": discovery.first_observed_at.isoformat() if discovery.first_observed_at else None,
                "last_seen": discovery.first_observed_at.isoformat() if discovery.first_observed_at else None,
                "score_breakdown": signal.score_breakdown or {},
                "explanation": "Oportunidade preliminar detectada no card de busca.",
                "investigation": {},
                "source": "olx" if "olx" in discovery.normalized_url else "fixture",
                "is_current": True,
                "last_pipeline_run_id": signal.pipeline_run_id,
                "profile_feedback": None,
            }
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "RESOURCE_NOT_FOUND", "message": f"Opportunity {id} not found", "details": None}}
        )

    opp, l, prod, ms = record

    feedback_context = _profile_feedback_context(db, profile)
    feedback = feedback_context["by_listing"].get(l.id)
    personalization = _personalization_fields(profile, prod, feedback_context, opp.final_score)
    return {
        "id": opp.id,
        "listing_id": l.id,
        "product_id": prod.id,
        "product_name": prod.display_name,
        "category": prod.category,
        "title": l.title,
        "description": l.description,
        "asking_price": l.price,
        "estimated_clearing_value": ms.estimated_clearing_value if ms else round(l.price * 1.2, 2),
        "fast_sale_value": ms.fast_sale_value if ms else round(l.price * 1.05, 2),
        "price_edge": opp.price_edge,
        "final_score": opp.final_score,
        "market_heat": opp.market_heat,
        "heat_band": opp.heat_band,
        "confidence": opp.confidence,
        "condition": l.condition,
        "location": f"{l.location_city}, {l.location_state}",
        "seller_name": l.seller_name,
        "delivery_status": l.delivery_status,
        "delivery_evidence": l.delivery_evidence or [],
        "seller_verification": l.seller_verification,
        "seller_signal_level": l.seller_signal_level,
        "seller_evidence": l.seller_evidence or [],
        "analysis_summary": l.analysis_summary,
        "analysis_status": l.analysis_status,
        "source_url": canonical_source_url(l.source_url),
        "first_seen": l.first_seen.isoformat() if l.first_seen else None,
        "last_seen": l.last_seen.isoformat() if l.last_seen else None,
        "score_breakdown": opp.score_breakdown,
        "explanation": opp.explanation,
        "investigation": opp.investigation
        ,"source": opp.source,
        "is_current": opp.is_current,
        "last_pipeline_run_id": opp.last_pipeline_run_id,
        "profile_feedback": _feedback_to_dict(feedback) if feedback else None,
        **personalization,
    }


@app.put("/api/v1/opportunities/{id}/feedback")
def put_opportunity_feedback(
    id: str,
    payload: OpportunityFeedbackPayload,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    opportunity = db.query(Opportunity).filter(Opportunity.id == id).first()
    if not opportunity:
        raise _resource_not_found("Opportunity")
    feedback = db.query(OpportunityFeedback).filter(
        OpportunityFeedback.profile_id == profile.id,
        OpportunityFeedback.listing_id == opportunity.listing_id,
    ).first()
    now = _utcnow()
    feedback_value = payload.resolved_feedback()
    feedback_metadata = payload.structured_metadata()
    if feedback is None:
        feedback = OpportunityFeedback(
            id=f"feedback-{uuid.uuid4().hex[:12]}",
            profile_id=profile.id,
            listing_id=opportunity.listing_id,
            feedback=feedback_value,
            note=payload.note,
            metadata_json=feedback_metadata,
            created_at=now,
            updated_at=now,
        )
        db.add(feedback)
    else:
        feedback.feedback = feedback_value
        feedback.note = payload.note
        feedback.metadata_json = feedback_metadata
        feedback.updated_at = now
    db.commit()
    db.refresh(feedback)
    return _feedback_to_dict(feedback)


@app.delete("/api/v1/opportunities/{id}/feedback")
def delete_opportunity_feedback(
    id: str,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    opportunity = db.query(Opportunity).filter(Opportunity.id == id).first()
    if not opportunity:
        raise _resource_not_found("Opportunity")
    feedback = db.query(OpportunityFeedback).filter(
        OpportunityFeedback.profile_id == profile.id,
        OpportunityFeedback.listing_id == opportunity.listing_id,
    ).first()
    if not feedback:
        raise _resource_not_found("Opportunity feedback")
    db.delete(feedback)
    db.commit()
    return {"id": id, "deleted": True}

# ----------------------------------------------------------------------
# 6. Pipelines
# ----------------------------------------------------------------------

def _scope_to_dict(scope: SearchScope) -> dict:
    return {
        "id": scope.id,
        "profile_id": scope.profile_id,
        "name": scope.name,
        "marketplace": scope.marketplace,
        "product_id": scope.product_id,
        "query": scope.query,
        "category": scope.category,
        "min_price": scope.min_price,
        "max_price": scope.max_price,
        "sort": scope.sort,
        "limit": scope.limit,
        "enabled": scope.enabled,
        "created_at": scope.created_at.isoformat() if scope.created_at else None,
        "updated_at": scope.updated_at.isoformat() if scope.updated_at else None,
        "product_name": scope.product.display_name if scope.product else None,
    }


def _ensure_default_scopes(
    db: Session,
    profile: Profile,
    marketplace: str = "olx",
) -> list[SearchScope]:
    if marketplace != "olx":
        return []
    owned_scopes = db.query(SearchScope).filter(
        SearchScope.profile_id == profile.id,
        SearchScope.marketplace == marketplace,
    )
    existing = owned_scopes.count()
    if existing:
        return owned_scopes.order_by(SearchScope.name).all()
    prefs = profile.preferences or {}
    max_capital = prefs.get("max_capital")
    target_categories = set(prefs.get("target_categories") or ["gpu", "notebook", "cpu", "ram", "ssd"])
    products = (
        db.query(Product)
        .filter(Product.canonical_tier == 1, Product.category.in_(target_categories))
        .order_by(Product.category, Product.display_name)
        .all()
    )
    now = _utcnow()
    for product in products:
        scope = SearchScope(
            id=f"scope-{uuid.uuid4().hex[:10]}",
            profile_id=profile.id,
            name=product.display_name,
            marketplace=marketplace,
            product_id=product.id,
            query=product.display_name,
            category=product.category,
            max_price=max_capital,
            sort="recent",
            limit=10,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        db.add(scope)
    db.commit()
    return (
        db.query(SearchScope)
        .filter(SearchScope.profile_id == profile.id, SearchScope.marketplace == marketplace)
        .order_by(SearchScope.name)
        .all()
    )


class SearchScopePayload(BaseModel):
    name: str
    marketplace: str = "olx"
    product_id: Optional[str] = None
    query: str
    category: Optional[str] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    require_price: bool = True
    olx_pay_only: bool = False
    delivery_only: bool = False
    sort: str = "recent"
    limit: int = Query(10, ge=1, le=100)
    enabled: bool = True


@app.get("/api/v1/search-scopes")
def get_search_scopes(
    marketplace: str = "olx",
    enabled_only: bool = False,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    scopes = _ensure_default_scopes(db, profile, marketplace)
    if enabled_only:
        scopes = [scope for scope in scopes if scope.enabled]
    return {"items": [_scope_to_dict(scope) for scope in scopes]}


@app.post("/api/v1/search-scopes", status_code=status.HTTP_201_CREATED)
def create_search_scope(
    payload: SearchScopePayload,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    if payload.marketplace not in {"olx", "fixture", "playwright_fixture"}:
        raise HTTPException(status_code=422, detail="marketplace inválido")
    data = {k: v for k, v in payload.model_dump().items() if hasattr(SearchScope, k)}
    scope = SearchScope(
        id=f"scope-{uuid.uuid4().hex[:10]}",
        profile_id=profile.id,
        **data,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(scope)
    db.commit()
    db.refresh(scope)
    return _scope_to_dict(scope)


@app.put("/api/v1/search-scopes/{scope_id}")
def update_search_scope(
    scope_id: str,
    payload: SearchScopePayload,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    scope = db.query(SearchScope).filter(
        SearchScope.id == scope_id,
        SearchScope.profile_id == profile.id,
    ).first()
    if not scope:
        raise _resource_not_found("Search scope")
    for key, value in payload.model_dump().items():
        if hasattr(scope, key):
            setattr(scope, key, value)
    scope.updated_at = _utcnow()
    db.commit()
    db.refresh(scope)
    return _scope_to_dict(scope)


@app.delete("/api/v1/search-scopes/{scope_id}")
def disable_search_scope(
    scope_id: str,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    scope = db.query(SearchScope).filter(
        SearchScope.id == scope_id,
        SearchScope.profile_id == profile.id,
    ).first()
    if not scope:
        raise _resource_not_found("Search scope")
    scope.enabled = False
    scope.updated_at = _utcnow()
    db.commit()
    return {"id": scope.id, "enabled": False}


# ----------------------------------------------------------------------
# Versioned search definitions and profile-owned search sessions
# ----------------------------------------------------------------------

def _definition_to_dict(definition: SearchDefinition) -> dict:
    return {
        "id": definition.id,
        "profile_id": definition.profile_id,
        "name": definition.name,
        "intent": definition.intent or {},
        "intent_version": definition.intent_version,
        "plan": definition.plan or {},
        "plan_version": definition.plan_version,
        "created_at": definition.created_at.isoformat() if definition.created_at else None,
        "updated_at": definition.updated_at.isoformat() if definition.updated_at else None,
    }


def _semantic_value(value: Any) -> Any:
    """Return a stable, human-input-insensitive value for scope comparison."""
    if isinstance(value, dict):
        return {
            str(key): _semantic_value(item)
            for key, item in sorted(value.items(), key=lambda entry: str(entry[0]))
        }
    if isinstance(value, list):
        items = [_semantic_value(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str))
    if isinstance(value, str):
        folded = unicodedata.normalize("NFKD", value)
        folded = "".join(char for char in folded if not unicodedata.combining(char))
        return " ".join(folded.casefold().split())
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _criteria_semantic_key(intent: dict, plan: dict, group: str) -> list[dict]:
    """Compare the executable criterion, not a model's prose or confidence."""
    raw_criteria = plan.get(group)
    if not isinstance(raw_criteria, list):
        raw_criteria = intent.get(group) if isinstance(intent.get(group), list) else []
    criteria: list[dict] = []
    for criterion in raw_criteria:
        if not isinstance(criterion, dict):
            continue
        criteria.append(_semantic_value({
            "field": criterion.get("field"),
            "operator": criterion.get("operator"),
            "value": criterion.get("value"),
            "unit": criterion.get("unit"),
        }))
    return sorted(criteria, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str))


def _normalise_definition_plan_for_olx(plan: dict) -> dict:
    """Bring saved plans to the same OLX-safe retrieval form as new drafts."""
    if not isinstance(plan, dict) or not plan:
        return plan if isinstance(plan, dict) else {}
    try:
        from packages.search import normalise_olx_retrieval_queries

        return normalise_olx_retrieval_queries(plan).model_dump(mode="json")
    except (ImportError, AttributeError, TypeError, ValueError):
        # Legacy fixture definitions intentionally remain readable even when
        # they do not satisfy the versioned SearchPlanV1 schema.
        return plan


def _definition_semantic_key(intent: dict, plan: dict) -> str:
    """Key a reusable scope by what it executes, excluding chat wording."""
    intent = intent if isinstance(intent, dict) else {}
    plan = plan if isinstance(plan, dict) else {}
    identity = {
        field: intent.get(field)
        for field in (
            "category", "brands", "families", "models", "generations",
            "budget", "location", "condition",
        )
        if intent.get(field) not in (None, "", [], {})
    }
    desired_count = plan.get("desired_count", intent.get("desired_count"))
    key: dict[str, Any] = {
        "identity": _semantic_value(identity),
        "desired_count": _semantic_value(desired_count),
        "must": _criteria_semantic_key(intent, plan, "must"),
        "should": _criteria_semantic_key(intent, plan, "should"),
        "must_not": _criteria_semantic_key(intent, plan, "must_not"),
    }
    if not any((key["identity"], key["must"], key["should"], key["must_not"])):
        # Legacy/manual definitions can lack structured fields. Keep their
        # text distinct while still ignoring case, whitespace and accents.
        key["free_text"] = _semantic_value(
            intent.get("raw_query") or intent.get("query") or plan.get("query") or ""
        )
    return json.dumps(key, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _find_equivalent_definition(
    db: Session,
    profile: Profile,
    intent: dict,
    plan: dict,
    *,
    exclude_id: Optional[str] = None,
) -> Optional[SearchDefinition]:
    expected_key = _definition_semantic_key(intent, plan)
    definitions = db.query(SearchDefinition).filter(
        SearchDefinition.profile_id == profile.id
    ).order_by(desc(SearchDefinition.updated_at), desc(SearchDefinition.created_at), desc(SearchDefinition.id)).all()
    for definition in definitions:
        if definition.id == exclude_id:
            continue
        if _definition_semantic_key(definition.intent or {}, definition.plan or {}) == expected_key:
            return definition
    return None


def _merge_duplicate_definitions(db: Session, profile: Profile) -> int:
    """Keep the newest definition for each executable scope and retain history."""
    definitions = db.query(SearchDefinition).filter(
        SearchDefinition.profile_id == profile.id
    ).order_by(desc(SearchDefinition.updated_at), desc(SearchDefinition.created_at), desc(SearchDefinition.id)).all()
    canonical_by_key: dict[str, SearchDefinition] = {}
    merged = 0
    for definition in definitions:
        normalized_plan = _normalise_definition_plan_for_olx(definition.plan or {})
        if normalized_plan != (definition.plan or {}):
            definition.plan = normalized_plan
        semantic_key = _definition_semantic_key(definition.intent or {}, definition.plan or {})
        canonical = canonical_by_key.get(semantic_key)
        if canonical is None:
            canonical_by_key[semantic_key] = definition
            continue
        db.query(SearchSession).filter(
            SearchSession.search_definition_id == definition.id
        ).update({SearchSession.search_definition_id: canonical.id}, synchronize_session=False)
        db.delete(definition)
        merged += 1
    return merged


class SearchDefinitionPayload(BaseModel):
    name: str
    intent: Dict[str, Any] = Field(default_factory=dict)
    intent_version: int = Field(default=1, ge=1)
    plan: Dict[str, Any] = Field(default_factory=dict)
    plan_version: int = Field(default=1, ge=1)


class SearchDefinitionUpdatePayload(BaseModel):
    name: Optional[str] = None
    intent: Optional[Dict[str, Any]] = None
    intent_version: Optional[int] = Field(default=None, ge=1)
    plan: Optional[Dict[str, Any]] = None
    plan_version: Optional[int] = Field(default=None, ge=1)


def _require_owned_definition(db: Session, profile: Profile, definition_id: str) -> SearchDefinition:
    definition = db.query(SearchDefinition).filter(
        SearchDefinition.id == definition_id,
        SearchDefinition.profile_id == profile.id,
    ).first()
    if not definition:
        raise _resource_not_found("Search definition")
    return definition


@app.get("/api/v1/search-definitions")
def list_search_definitions(
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    definitions = db.query(SearchDefinition).filter(
        SearchDefinition.profile_id == profile.id
    ).order_by(desc(SearchDefinition.updated_at), SearchDefinition.name).all()
    return {"items": [_definition_to_dict(definition) for definition in definitions]}


@app.post("/api/v1/search-definitions", status_code=status.HTTP_201_CREATED)
def create_search_definition(
    payload: SearchDefinitionPayload,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    name = " ".join(payload.name.strip().split())
    if not name:
        raise HTTPException(status_code=422, detail={"code": "SEARCH_NAME_REQUIRED", "message": "Search name is required"})
    now = _utcnow()
    intent = payload.intent or {}
    plan = _normalise_definition_plan_for_olx(payload.plan or {})
    definition = _find_equivalent_definition(db, profile, intent, plan)
    if definition is None:
        definition = SearchDefinition(
            id=f"search-def-{uuid.uuid4().hex[:12]}",
            profile_id=profile.id,
            name=name,
            intent=intent,
            intent_version=payload.intent_version,
            plan=plan,
            plan_version=payload.plan_version,
            created_at=now,
            updated_at=now,
        )
        db.add(definition)
    else:
        definition.name = name
        definition.intent = intent
        definition.intent_version = payload.intent_version
        definition.plan = plan
        definition.plan_version = payload.plan_version
        definition.updated_at = now
    _merge_duplicate_definitions(db, profile)
    db.commit()
    db.refresh(definition)
    return _definition_to_dict(definition)


@app.post("/api/v1/search-definitions/deduplicate")
def deduplicate_search_definitions(
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    """Consolidate legacy duplicate scope records without deleting sessions."""
    profile = _require_profile(db, x_profile_id)
    merged = _merge_duplicate_definitions(db, profile)
    db.commit()
    definitions = db.query(SearchDefinition).filter(
        SearchDefinition.profile_id == profile.id
    ).order_by(desc(SearchDefinition.updated_at), SearchDefinition.name).all()
    return {"merged": merged, "items": [_definition_to_dict(definition) for definition in definitions]}


@app.get("/api/v1/search-definitions/{definition_id}")
def get_search_definition(
    definition_id: str,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    return _definition_to_dict(_require_owned_definition(db, profile, definition_id))


@app.put("/api/v1/search-definitions/{definition_id}")
@app.patch("/api/v1/search-definitions/{definition_id}")
def update_search_definition(
    definition_id: str,
    payload: SearchDefinitionUpdatePayload,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    definition = _require_owned_definition(db, profile, definition_id)
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes:
        name = " ".join((changes["name"] or "").strip().split())
        if not name:
            raise HTTPException(status_code=422, detail={"code": "SEARCH_NAME_REQUIRED", "message": "Search name is required"})
        definition.name = name
    for field_name in ("intent", "intent_version", "plan", "plan_version"):
        if field_name in changes:
            setattr(definition, field_name, changes[field_name])
    definition.updated_at = _utcnow()
    db.commit()
    db.refresh(definition)
    return _definition_to_dict(definition)


@app.delete("/api/v1/search-definitions/{definition_id}")
def delete_search_definition(
    definition_id: str,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    definition = _require_owned_definition(db, profile, definition_id)
    # Existing sessions are historical records.  Preserve them and make their
    # definition link null rather than cascading deletion through a profile.
    db.query(SearchSession).filter(
        SearchSession.search_definition_id == definition.id
    ).update({SearchSession.search_definition_id: None}, synchronize_session=False)
    db.delete(definition)
    db.commit()
    return {"id": definition_id, "deleted": True}


def _normalise_compiled_search(raw: Any, current_intent: dict, text: str) -> dict:
    """Keep the HTTP persistence contract stable across optional search engines."""
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    raw = raw if isinstance(raw, dict) else {}
    intent = raw.get("intent") if isinstance(raw.get("intent"), dict) else current_intent
    intent = dict(intent or {})
    if text and not intent.get("query"):
        intent["query"] = text
    # Model gateways return a SearchPlanV1 directly, while the compatibility
    # service returns {intent, plan, status}. Normalize both shapes.
    plan = raw if raw.get("schema_version") == "search-plan-v1" else (raw.get("plan") if isinstance(raw.get("plan"), dict) else {})
    if plan:
        # A structured model can understand every constraint yet still return
        # prose-like search strings. The OLX browser accepts a category path
        # plus short title terms; normalize those terms before exposing or
        # saving the plan so the user sees the exact executable scope.
        try:
            from packages.search import normalise_olx_retrieval_queries

            normalized_plan = normalise_olx_retrieval_queries(plan)
            plan = normalized_plan.model_dump(mode="json")
            intent = plan.get("intent") or intent
        except (ImportError, AttributeError, TypeError, ValueError):
            pass
    clarification_value = raw.get("clarification")
    if not clarification_value and isinstance(plan, dict) and plan.get("clarifications"):
        clarification_value = {"message": plan["clarifications"][0], "fields": []}
    clarification = _clarification_dict(clarification_value)
    status_value = raw.get("status")
    if not status_value:
        status_value = "needs_clarification" if clarification else "ready"
    return {
        "intent": intent,
        "plan": plan,
        "status": str(status_value),
        "clarification": clarification,
        "compiler": str(raw.get("compiler") or "local"),
    }


def _clarification_dict(value: Any) -> dict:
    """Normalise compiler-specific clarification into the public JSON contract."""
    if not value:
        return {}
    if isinstance(value, dict):
        result = dict(value)
        result["code"] = str(result.get("code") or "SEARCH_CLARIFICATION_REQUIRED")
        result["message"] = str(result.get("message") or "More information is required to compile this search")
        fields = result.get("fields") or []
        result["fields"] = fields if isinstance(fields, list) else [str(fields)]
        return result
    if isinstance(value, list):
        return {
            "code": "SEARCH_CLARIFICATION_REQUIRED",
            "message": "More information is required to compile this search",
            "fields": [str(item) for item in value],
        }
    return {
        "code": "SEARCH_CLARIFICATION_REQUIRED",
        "message": str(value),
        "fields": [],
    }


def _compile_search_local(text: str, current_intent: Optional[dict] = None) -> dict:
    """Use packages.search when installed, with a deterministic persistence fallback.

    Expected optional interface: ``compile_search_local(text,
    current_intent=None) -> mapping|Pydantic model`` with intent, plan,
    status and optional clarification keys.  The fallback deliberately does
    not invent retrieval/scoring logic; it stores enough structured state for
    a client to complete a local fixture plan.
    """
    current_intent = current_intent or {}
    compiler = None
    try:
        from packages.search import compile_search_local as compiler  # type: ignore
    except (ImportError, AttributeError):
        try:
            from packages.search.service import compile_search_local as compiler  # type: ignore
        except (ImportError, AttributeError):
            compiler = None
    if compiler is not None:
        try:
            return _normalise_compiled_search(compiler(text, current_intent=current_intent), current_intent, text)
        except TypeError:
            # Supports the equivalent positional-current-intent implementation
            # while the package API settles, without hiding genuine compiler
            # errors from the response state below.
            try:
                return _normalise_compiled_search(compiler(text, current_intent), current_intent, text)
            except Exception as exc:
                return {
                    "intent": {**current_intent, **({"query": text} if text else {})},
                    "plan": {},
                    "status": "draft",
                    "clarification": None,
                    "compiler": f"fallback_after_{type(exc).__name__}",
                }
        except Exception as exc:
            return {
                "intent": {**current_intent, **({"query": text} if text else {})},
                "plan": {},
                "status": "draft",
                "clarification": None,
                "compiler": f"fallback_after_{type(exc).__name__}",
            }
    return {
        "intent": {**current_intent, **({"query": text} if text else {})},
        "plan": {},
        "status": "draft",
        "clarification": None,
        "compiler": "persistence_fallback",
    }


async def _compile_search_with_model(text: str, current_intent: Optional[dict] = None) -> dict:
    """Compile through the configured gateway, with a deterministic fallback.

    The gateway owns provider-specific structured output and validation.  The
    API only persists its complete plan-shaped result so local mode remains
    credential-free and live mode can use the same session contract.
    """
    try:
        gateway = get_model_gateway()
        plan = await gateway.compile_search_intent(text, current_intent=current_intent or {})
        return _normalise_compiled_search(plan, current_intent or {}, text)
    except Exception:
        return _compile_search_local(text, current_intent=current_intent)


def _session_to_dict(session: SearchSession, include_messages: bool = True) -> dict:
    draft = session.draft or {}
    definition = session.search_definition
    saved_intent = definition.intent if definition else None
    saved_plan = definition.plan if definition else None
    has_unsaved_changes = bool(definition and (
        (draft.get("intent") or {}) != (saved_intent or {})
        or (draft.get("plan") or {}) != (saved_plan or {})
    ))
    result = {
        "id": session.id,
        "profile_id": session.profile_id,
        "search_definition_id": session.search_definition_id,
        "pipeline_run_id": session.pipeline_run_id,
        "status": session.status,
        "intent": draft.get("intent") or {},
        "intent_version": int(draft.get("intent_version") or 1),
        "plan": draft.get("plan") or {},
        "plan_version": int(draft.get("plan_version") or 1),
        "clarification": session.clarification or {},
        "draft": draft,
        "confirmed_plan": session.confirmed_plan,
        "execution": session.execution or {},
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        "is_saved": bool(definition),
        "has_unsaved_changes": has_unsaved_changes,
        "saved_definition_name": definition.name if definition else None,
    }
    if include_messages:
        result["messages"] = [
            {
                "id": message.id,
                "client_request_id": message.client_request_id,
                "role": message.role,
                "content": message.content,
                "metadata": message.metadata_json or {},
                "created_at": message.created_at.isoformat() if message.created_at else None,
            }
            for message in session.messages
        ]
    return result


def _require_owned_session(db: Session, profile: Profile, session_id: str) -> SearchSession:
    session = db.query(SearchSession).filter(
        SearchSession.id == session_id,
        SearchSession.profile_id == profile.id,
    ).first()
    if not session:
        raise _resource_not_found("Search session")
    return session


class SearchSessionCreatePayload(BaseModel):
    search_definition_id: Optional[str] = None
    text: str = ""
    draft: Dict[str, Any] = Field(default_factory=dict)


class SearchSessionMessagePayload(BaseModel):
    # Optional for older callers. When present it is an idempotency key scoped
    # to this search session and a replay never recompiles the user message.
    client_request_id: Optional[str] = None
    content: str
    role: Literal["user", "assistant", "system"] = "user"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SearchSessionDraftPayload(BaseModel):
    draft: Dict[str, Any] = Field(default_factory=dict)
    intent: Optional[Dict[str, Any]] = None
    plan: Optional[Dict[str, Any]] = None
    clarification: Optional[Any] = None


class SearchSessionConfirmPayload(BaseModel):
    plan: Optional[Dict[str, Any]] = None


class SearchSessionSavePayload(BaseModel):
    name: Optional[str] = None


@app.get("/api/v1/search-sessions")
def list_search_sessions(
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    sessions = db.query(SearchSession).filter(
        SearchSession.profile_id == profile.id
    ).order_by(desc(SearchSession.updated_at)).all()
    return {"items": [_session_to_dict(session, include_messages=False) for session in sessions]}


@app.post("/api/v1/search-sessions", status_code=status.HTTP_201_CREATED)
async def create_search_session(
    payload: SearchSessionCreatePayload,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    definition = None
    if payload.search_definition_id:
        definition = _require_owned_definition(db, profile, payload.search_definition_id)
    draft = dict(payload.draft or {})
    if definition:
        draft.setdefault("intent", definition.intent or {})
        draft.setdefault("intent_version", definition.intent_version)
        draft.setdefault("plan", definition.plan or {})
        draft.setdefault("plan_version", definition.plan_version)
    if payload.text.strip():
        compiled = await _compile_search_with_model(payload.text.strip(), draft.get("intent") or {})
        draft.update(compiled)
    now = _utcnow()
    session = SearchSession(
        id=f"search-session-{uuid.uuid4().hex[:12]}",
        profile_id=profile.id,
        search_definition_id=definition.id if definition else None,
        status=str(draft.get("status") or "draft"),
        draft=draft,
        clarification=_clarification_dict(draft.get("clarification")),
        execution={},
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    if payload.text.strip():
        db.add(SearchSessionMessage(
            id=f"search-msg-{uuid.uuid4().hex[:12]}",
            search_session_id=session.id,
            role="user",
            content=payload.text.strip(),
            metadata_json={},
            created_at=now,
        ))
    db.commit()
    db.refresh(session)
    return _session_to_dict(session)


@app.get("/api/v1/search-sessions/{session_id}")
def get_search_session(
    session_id: str,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    return _session_to_dict(_require_owned_session(db, profile, session_id))


def _generate_search_assistant_reply(
    session: SearchSession,
    user_message: str,
    intent: dict,
    plan: dict,
    clarification: Optional[dict] = None,
) -> str:
    query = intent.get("query") or intent.get("raw_query") or user_message
    category = intent.get("category")
    max_price = intent.get("max_price") or (plan.get("budget") or {}).get("max")
    location = intent.get("location") or (plan.get("location") or {}).get("state")
    condition = intent.get("condition") or (plan.get("condition") or {}).get("value")

    try:
        mg = get_model_gateway()
        if isinstance(mg, VertexModelGateway):
            client = mg._get_client()
            recent = [
                f"{'Usuário' if m.role == 'user' else 'Assistente'}: {m.content}"
                for m in (session.messages or [])[-6:]
            ]
            history_str = "\n".join(recent)
            prompt = (
                "Você é o assistente de busca do Market Radar para o marketplace OLX (foco em hardware, tecnologia e computadores).\n"
                "O usuário está conversando para planejar ou refinar a busca que deseja realizar.\n\n"
                f"Histórico recente:\n{history_str}\n\n"
                f"Última mensagem do usuário: {user_message}\n\n"
                f"Critérios detectados:\n"
                f"- Produto/Termo: {query}\n"
                f"- Categoria: {category or 'não especificada'}\n"
                f"- Preço máximo: {f'R$ {max_price}' if max_price else 'não definido'}\n"
                f"- Localização: {location or 'Brasil (geral)'}\n"
                f"- Condição: {condition or 'usado/qualquer'}\n"
                f"- Esclarecimento: {clarification.get('message') if (clarification and isinstance(clarification, dict)) else 'nenhum'}\n\n"
                "Instruções:\n"
                "1. Seja limpo, direto e objetivo (máximo de 2 parágrafos curtos).\n"
                "2. Confirme os critérios identificados (produto, preço, local ou condição).\n"
                "3. Se faltar preço máximo ou local, sugira valores realistas ou faça perguntas objetivas.\n"
                "4. Se tudo estiver configurado, confirme que o escopo está pronto para ser salvo e preparado na tela de Pipelines.\n"
                "5. Evite saudações longas, clichês ou texto cru."
            )
            response = client.models.generate_content(
                model=mg.model_chat,
                contents=prompt,
                config={"temperature": 0.2},
            )
            reply_text = getattr(response, "text", "")
            if reply_text and reply_text.strip():
                return reply_text.strip()
    except Exception:
        pass

    parts = []
    if query:
        parts.append(f"Entendido! Configurei os critérios para **{query}**.")
    else:
        parts.append("Parâmetros da busca atualizados.")

    filters = []
    if category:
        filters.append(f"Categoria: {category}")
    if max_price:
        try:
            formatted_price = f"R$ {float(max_price):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            filters.append(f"Preço máximo: {formatted_price}")
        except Exception:
            filters.append(f"Preço máximo: R$ {max_price}")
    if location:
        filters.append(f"Local: {location}")
    if condition:
        filters.append(f"Condição: {condition}")

    if filters:
        parts.append("Filtros ativos: " + " · ".join(filters) + ".")

    if clarification and isinstance(clarification, dict) and clarification.get("message"):
        parts.append(f"Atenção: {clarification['message']}.")
    else:
        parts.append("Tudo pronto! Você pode ajustar os filtros aqui, salvar o escopo e prepará-lo na tela de Pipelines.")

    return "\n\n".join(parts)


@app.post("/api/v1/search-sessions/{session_id}/messages")
async def post_search_session_message(
    session_id: str,
    payload: SearchSessionMessagePayload,
    reply: bool = Query(default=False),
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    session = _require_owned_session(db, profile, session_id)
    client_request_id = (payload.client_request_id or "").strip() or None
    if client_request_id:
        replay = db.query(SearchSessionMessage).filter(
            SearchSessionMessage.search_session_id == session.id,
            SearchSessionMessage.client_request_id == client_request_id,
        ).first()
        if replay:
            return _session_to_dict(session)
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail={"code": "MESSAGE_REQUIRED", "message": "Message content is required"})
    now = _utcnow()
    message = SearchSessionMessage(
        id=f"search-msg-{uuid.uuid4().hex[:12]}",
        search_session_id=session.id,
        client_request_id=client_request_id,
        role=payload.role,
        content=content,
        metadata_json=payload.metadata or {},
        created_at=now,
    )
    db.add(message)
    if client_request_id:
        # Claim the request key before calling the optional compiler. A racing
        # duplicate cannot create another message or trigger another compile.
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            replayed_session = _require_owned_session(db, profile, session_id)
            return _session_to_dict(replayed_session)
    # User text is the only input sent to the optional compiler. Assistant and
    # system messages are persisted verbatim for an auditable conversation.
    if payload.role == "user":
        compiled = await _compile_search_with_model(content, (session.draft or {}).get("intent") or {})
        draft = dict(session.draft or {})
        draft.update(compiled)
        session.draft = draft
        session.status = compiled["status"]
        session.clarification = _clarification_dict(compiled.get("clarification"))
        if reply:
            assistant_reply = _generate_search_assistant_reply(
                session=session,
                user_message=content,
                intent=draft.get("intent") or {},
                plan=draft.get("plan") or {},
                clarification=session.clarification,
            )
            if assistant_reply:
                assistant_msg = SearchSessionMessage(
                    id=f"search-msg-{uuid.uuid4().hex[:12]}",
                    search_session_id=session.id,
                    role="assistant",
                    content=assistant_reply,
                    metadata_json={"intent": draft.get("intent"), "plan": draft.get("plan")},
                    created_at=_utcnow(),
                )
                db.add(assistant_msg)
    session.updated_at = now
    db.commit()
    db.refresh(session)
    return _session_to_dict(session)


@app.post("/api/v1/search-sessions/{session_id}/chat")
async def chat_search_session(
    session_id: str,
    payload: SearchSessionMessagePayload,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    return await post_search_session_message(session_id, payload, reply=True, x_profile_id=x_profile_id, db=db)


def _search_definition_name(session: SearchSession) -> str:
    draft = session.draft or {}
    intent = draft.get("intent") or {}
    plan = _normalise_definition_plan_for_olx(draft.get("plan") or {})
    labels: list[str] = []
    product_value = next((intent.get(key) for key in ("models", "families", "brands", "category") if intent.get(key)), "")
    product = ", ".join(str(item) for item in product_value[:2]) if isinstance(product_value, list) else str(product_value).strip()
    if product:
        labels.append(product)
    criteria = plan.get("must") if isinstance(plan, dict) else []
    field_labels = {"panel_type": "Tela", "wifi_bands": "Wi-Fi", "ram_gb": "RAM", "ssd_gb": "SSD"}
    for criterion in criteria or []:
        if not isinstance(criterion, dict):
            continue
        field = str(criterion.get("field") or "")
        value = criterion.get("value")
        if field in field_labels and value not in (None, "", []):
            display = ", ".join(str(item) for item in value) if isinstance(value, list) else str(value)
            labels.append(f"{field_labels[field]} {display}")
    budget = (intent.get("budget") or {}) if isinstance(intent, dict) else {}
    if budget.get("max") is not None:
        labels.append(f"até R$ {float(budget['max']):,.0f}".replace(",", "."))
    return " · ".join(labels)[:120] or "Busca personalizada"


@app.post("/api/v1/search-sessions/{session_id}/save", status_code=status.HTTP_201_CREATED)
def save_search_session(
    session_id: str,
    payload: Optional[SearchSessionSavePayload] = None,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    session = _require_owned_session(db, profile, session_id)
    draft = session.draft or {}
    if session.clarification:
        raise HTTPException(status_code=409, detail={"code": "SEARCH_CLARIFICATION_REQUIRED", "message": "Resolve the outstanding clarification before saving the search", "clarification": session.clarification})
    intent = draft.get("intent") or {}
    plan = draft.get("plan") or {}
    if not isinstance(plan, dict) or not plan:
        raise HTTPException(status_code=422, detail={"code": "SEARCH_PLAN_REQUIRED", "message": "A non-empty search plan is required before saving"})
    plan = _normalise_definition_plan_for_olx(plan)
    name = ((payload.name if payload else None) or _search_definition_name(session)).strip()
    name = " ".join(name.split())[:120] or "Busca personalizada"
    now = _utcnow()
    definition = session.search_definition
    equivalent = _find_equivalent_definition(
        db,
        profile,
        intent,
        plan,
        exclude_id=definition.id if definition else None,
    )
    if equivalent is not None:
        definition = equivalent
        session.search_definition_id = definition.id
    created = definition is None
    if definition is None:
        definition = SearchDefinition(
            id=f"search-def-{uuid.uuid4().hex[:12]}",
            profile_id=profile.id,
            name=name,
            intent=intent,
            intent_version=int(draft.get("intent_version") or 1),
            plan=plan,
            plan_version=int(draft.get("plan_version") or 1),
            created_at=now,
            updated_at=now,
        )
        db.add(definition)
        session.search_definition_id = definition.id
    else:
        if payload and payload.name is not None:
            definition.name = name
        definition.intent = intent
        definition.plan = plan
        definition.intent_version = int(draft.get("intent_version") or definition.intent_version or 1)
        definition.plan_version = int(draft.get("plan_version") or definition.plan_version or 1)
        definition.updated_at = now
    session.updated_at = now
    _merge_duplicate_definitions(db, profile)
    db.commit()
    db.refresh(definition)
    db.refresh(session)
    return {"created": created, "definition": _definition_to_dict(definition), "session": _session_to_dict(session)}


@app.get("/api/v1/search-sessions/{session_id}/results")
def get_search_session_results(
    session_id: str,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    session = _require_owned_session(db, profile, session_id)
    run = None
    if session.pipeline_run_id:
        run = db.query(PipelineRun).filter(PipelineRun.id == session.pipeline_run_id, PipelineRun.profile_id == profile.id).first()
    groups: dict[str, list[dict[str, Any]]] = {"confirmed": [], "unverified": []}
    rejected = 0
    if run:
        opportunities = {
            listing_id: opportunity
            for opportunity, listing_id in db.query(Opportunity, Opportunity.listing_id).filter(Opportunity.last_pipeline_run_id == run.id).all()
        }
        for scope in run.scope_runs:
            for item in scope.items:
                status_value = item.match_status or ("confirmed" if item.status == "processed" else "rejected" if item.status in {"rejected", "scope_mismatch", "recommendation_filtered"} else None)
                if status_value not in groups:
                    if status_value == "rejected":
                        rejected += 1
                    continue
                listing = item.listing
                if listing is None:
                    continue
                product = db.query(Product).filter(Product.id == listing.product_id).first() if listing.product_id else None
                opportunity = opportunities.get(listing.id)
                groups[status_value].append({
                    "id": listing.id,
                    "external_id": item.external_id,
                    "title": listing.title,
                    "product_name": product.display_name if product else None,
                    "price": listing.price,
                    "location": f"{listing.location_city}, {listing.location_state}".strip(", "),
                    "condition": listing.condition,
                    "source_url": canonical_source_url(listing.source_url),
                    "attributes": listing.attributes or {},
                    "match": item.match_details or {},
                    "opportunity_id": opportunity.id if opportunity else None,
                    "score": opportunity.final_score if opportunity else None,
                    "price_edge": opportunity.price_edge if opportunity else None,
                })
    return {
        "session_id": session.id,
        "pipeline_id": session.pipeline_run_id,
        "pipeline_status": run.status if run else session.status,
        "counts": {"confirmed": len(groups["confirmed"]), "unverified": len(groups["unverified"]), "rejected": rejected},
        "confirmed": groups["confirmed"],
        "unverified": groups["unverified"],
    }


@app.put("/api/v1/search-sessions/{session_id}/draft")
@app.patch("/api/v1/search-sessions/{session_id}/draft")
def update_search_session_draft(
    session_id: str,
    payload: SearchSessionDraftPayload,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    session = _require_owned_session(db, profile, session_id)
    draft = dict(session.draft or {})
    draft.update(payload.draft or {})
    if payload.intent is not None:
        draft["intent"] = payload.intent
    if payload.plan is not None:
        draft["plan"] = payload.plan
    if "clarification" in payload.model_fields_set:
        draft["clarification"] = _clarification_dict(payload.clarification)
    session.draft = draft
    session.clarification = _clarification_dict(draft.get("clarification"))
    session.status = "needs_clarification" if session.clarification else "draft"
    session.updated_at = _utcnow()
    db.commit()
    db.refresh(session)
    return _session_to_dict(session)


@app.post("/api/v1/search-sessions/{session_id}/confirm")
def confirm_search_session(
    session_id: str,
    payload: Optional[SearchSessionConfirmPayload] = None,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    session = _require_owned_session(db, profile, session_id)
    payload = payload or SearchSessionConfirmPayload()
    draft = session.draft or {}
    if session.clarification:
        raise HTTPException(
            status_code=409,
            detail={"code": "SEARCH_CLARIFICATION_REQUIRED", "message": "Resolve the outstanding clarification before confirmation", "clarification": session.clarification},
        )
    plan = payload.plan if payload.plan is not None else draft.get("plan")
    if not isinstance(plan, dict) or not plan:
        raise HTTPException(status_code=422, detail={"code": "SEARCH_PLAN_REQUIRED", "message": "A non-empty search plan is required before confirmation"})
    session.confirmed_plan = plan
    session.status = "confirmed"
    session.updated_at = _utcnow()
    db.commit()
    db.refresh(session)
    return _session_to_dict(session)


def _pipeline_configuration_for_session(db: Session, profile: Profile, session: SearchSession) -> tuple[str, dict]:
    plan = session.confirmed_plan or {}
    draft = session.draft or {}
    intent = draft.get("intent") or {}
    plan_intent = plan.get("intent") if isinstance(plan.get("intent"), dict) else {}
    source_type = str(
        plan.get("source_type")
        or plan.get("marketplace")
        or (os.getenv("MARKETPLACE_SOURCE", "olx") if os.getenv("APP_MODE", "local").lower() == "live" else "fixture")
    )
    if source_type not in {"fixture", "olx", "playwright_fixture"}:
        raise HTTPException(status_code=422, detail={"code": "SEARCH_SOURCE_INVALID", "message": "Unsupported search plan source"})
    if source_type == "olx" and os.getenv("APP_MODE", "local").lower() != "live":
        raise HTTPException(status_code=409, detail={"code": "OLX_OPT_IN_REQUIRED", "message": "OLX requires APP_MODE=live"})
    scope_ids = plan.get("scope_ids") or []
    scopes = plan.get("scopes") or []
    if scope_ids:
        selected = db.query(SearchScope).filter(
            SearchScope.id.in_(scope_ids),
            SearchScope.profile_id == profile.id,
            SearchScope.enabled.is_(True),
        ).all()
        selected_by_id = {scope.id: scope for scope in selected}
        if any(scope_id not in selected_by_id for scope_id in scope_ids):
            raise _resource_not_found("Search scope")
        scopes = [_scope_to_dict(selected_by_id[scope_id]) for scope_id in scope_ids]
    if not isinstance(scopes, list):
        raise HTTPException(status_code=422, detail={"code": "SEARCH_PLAN_INVALID", "message": "Plan scopes must be a list"})
    query = str(
        plan.get("query")
        or intent.get("query")
        or intent.get("raw_query")
        or plan_intent.get("raw_query")
        or ""
    )
    limit = int(plan.get("limit") or plan.get("desired_count") or intent.get("desired_count") or 5)
    limit = max(1, min(limit, 100))
    if not scopes and plan.get("schema_version"):
        # Keep the API a persistence adapter: the optional deterministic
        # search package owns conversion of its versioned plan into scopes.
        try:
            from packages.search import prepare_pipeline_scopes  # type: ignore
            scopes = prepare_pipeline_scopes(plan, marketplace=source_type)
        except (ImportError, AttributeError, ValueError, TypeError):
            scopes = []
    if not scopes:
        scopes = [{
            "id": None,
            "name": "Busca de sessão",
            "marketplace": source_type,
            "query": query,
            "category": intent.get("category"),
            "min_price": intent.get("min_price"),
            "max_price": intent.get("max_price"),
            "sort": str(plan.get("sort") or "recent"),
            "limit": limit,
            "enabled": True,
        }]
    return source_type, {
        "source_type": source_type,
        "fixture_version": str(plan.get("fixture_version") or "v1.0.0"),
        "query": query,
        "limit": limit,
        "model_mode": str(plan.get("model_mode") or "auto"),
        "investigate_limit": max(0, min(int(plan.get("investigate_limit") or 2), 10)),
        "scope_ids": scope_ids,
        "scopes": scopes,
        "search_session_id": session.id,
    }


@app.post("/api/v1/search-sessions/{session_id}/execute")
def execute_search_session(
    session_id: str,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    # Kept as an explicit compatibility response so older clients do not
    # silently queue work from the search workspace. A search refines and
    # saves a scope; Pipelines is the only execution surface.
    _require_owned_session(db, _require_profile(db, x_profile_id), session_id)
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "PIPELINE_EXECUTION_REQUIRES_PIPELINES_SCREEN",
            "message": "Salve o escopo e execute-o pela tela de Pipelines.",
        },
    )

@app.get("/api/v1/pipelines")
def get_pipelines(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db)
):
    profile = _require_profile(db, x_profile_id)
    query = db.query(PipelineRun).filter(PipelineRun.profile_id == profile.id)
    total = query.count()
    runs = query.order_by(desc(PipelineRun.started_at)).offset((page - 1) * page_size).limit(page_size).all()

    chat_service = PipelineChatService(db)
    items = [
        {
            "id": p.id,
            "type": p.type,
            "status": p.status,
            "started_at": (p.started_at.isoformat() + "Z") if p.started_at and not p.started_at.isoformat().endswith("Z") else (p.started_at.isoformat() if p.started_at else None),
            "finished_at": (p.finished_at.isoformat() + "Z") if p.finished_at and not p.finished_at.isoformat().endswith("Z") else (p.finished_at.isoformat() if p.finished_at else None),
            "duration_seconds": p.duration_seconds,
            "processed_count": p.processed_count,
            "snapshots_created": p.snapshots_created,
            "products_normalized": p.products_normalized,
            "opportunities_found": p.opportunities_found,
            "error_code": p.error_code,
            "error_message": p.error_message,
            "workload": _pipeline_workload_public(p),
            "dataset_analysis": _pipeline_dataset_analysis_public(p.dataset_analysis),
            "chat": chat_service.compact_eligibility_for(p),
            "steps": p.steps,
            "scopes": [
                {
                    "id": scope.id,
                    "search_scope_id": scope.search_scope_id,
                    "scope": scope.scope_snapshot,
                    "status": scope.status,
                    "search_url": scope.search_url,
                    "total_results": scope.total_results,
                    "cards_detected": scope.cards_detected,
                    "items_returned": scope.items_returned,
                    "processed_count": scope.processed_count,
                    "error_code": scope.error_code,
                    "error_message": scope.error_message,
                }
                for scope in p.scope_runs
            ]
        }
        for p in runs
    ]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size)
    }

@app.get("/api/v1/pipelines/{id}")
def get_pipeline(
    id: str,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    p = db.query(PipelineRun).filter(
        PipelineRun.id == id,
        PipelineRun.profile_id == profile.id,
    ).first()
    if not p:
        raise _resource_not_found("Pipeline run")
    return {
        "id": p.id,
        "type": p.type,
        "status": p.status,
        "started_at": (p.started_at.isoformat() + "Z") if p.started_at and not p.started_at.isoformat().endswith("Z") else (p.started_at.isoformat() if p.started_at else None),
        "finished_at": (p.finished_at.isoformat() + "Z") if p.finished_at and not p.finished_at.isoformat().endswith("Z") else (p.finished_at.isoformat() if p.finished_at else None),
        "duration_seconds": p.duration_seconds,
        "processed_count": p.processed_count,
        "snapshots_created": p.snapshots_created,
        "products_normalized": p.products_normalized,
        "opportunities_found": p.opportunities_found,
        "error_code": p.error_code,
        "error_message": p.error_message,
        "workload": _pipeline_workload_public(p),
        "dataset_analysis": _pipeline_dataset_analysis_public(p.dataset_analysis),
        "steps": p.steps,
        "scopes": [
            {
                "id": scope.id,
                "search_scope_id": scope.search_scope_id,
                "scope": scope.scope_snapshot,
                "status": scope.status,
                "search_url": scope.search_url,
                "total_results": scope.total_results,
                "cards_detected": scope.cards_detected,
                "items_returned": scope.items_returned,
                "processed_count": scope.processed_count,
                "error_code": scope.error_code,
                "error_message": scope.error_message,
                "items": [
                    {
                        "external_id": item.external_id,
                        "listing_id": item.listing_id,
                        "rank": item.rank,
                        "status": item.status,
                        "error_message": item.error_message,
                    }
                    for item in scope.items
                ],
            }
            for scope in p.scope_runs
        ]
    }


def _pipeline_dataset_analysis_public(analysis: Optional[PipelineDatasetAnalysis]) -> Optional[dict]:
    """Expose aggregate evidence only; source cards and identifiers stay internal."""
    if analysis is None:
        return None
    result = analysis.result or {}
    observed = result.get("observed") or {}
    quality = result.get("capture_quality") or {}
    market = result.get("derived_market") or {}
    readiness = result.get("backend_readiness") or {}
    return {
        "status": analysis.status,
        "agent_status": analysis.agent_status,
        "agent_summary": analysis.agent_summary,
        "completed_at": analysis.completed_at.isoformat() if analysis.completed_at else None,
        "observed": observed,
        "capture_quality": quality,
        "derived_market": market,
        "backend_readiness": readiness,
    }


def _incomplete_discovery_title(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<\s*br\s*/?\s*>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _incomplete_discovery_public(discovery: ListingDiscovery) -> dict:
    """Public card evidence; intentionally excludes internal discovery IDs."""
    result = dict(discovery.card_analysis_result or {})
    attributes = result.get("extracted_attributes") or {}
    safe_attributes = [
        {"name": str(name)[:80], "value": str(value)[:160]}
        for name, value in attributes.items()
        if isinstance(value, (str, int, float, bool))
    ][:8]
    missing = []
    if not discovery.price:
        missing.append("Preço no card")
    if not discovery.location:
        missing.append("Localização")
    if not discovery.condition:
        missing.append("Condição")
    if not discovery.seller:
        missing.append("Vendedor")
    if not discovery.marketplace_item_id:
        missing.append("Identificador do anúncio")
    enrichment = discovery.enrichment_task
    signal = discovery.opportunity_signal
    return {
        "title": _incomplete_discovery_title(discovery.title),
        "price": discovery.price if discovery.price is not None and discovery.price > 0 else None,
        "location": discovery.location or None,
        "source_url": canonical_source_url(discovery.normalized_url or discovery.external_id),
        "observed_at": discovery.first_observed_at.isoformat() if discovery.first_observed_at else None,
        "detail_status": enrichment.status if enrichment else "not_requested",
        "preliminary_score": signal.preliminary_score if signal else None,
        "opportunity_stage": signal.stage if signal else None,
        "full_flow_completed": bool(signal and signal.full_flow_completed),
        "analysis": {
            "status": discovery.card_analysis_status,
            "model": discovery.card_analysis_model_id,
            "category": result.get("category"),
            "brand": result.get("brand"),
            "model_name": result.get("model"),
            "variant": result.get("variant"),
            "confidence": result.get("confidence"),
            "summary": result.get("listing_summary"),
            "risk_flags": result.get("listing_risk_flags") or [],
            "attributes": safe_attributes,
            "error": discovery.card_analysis_error,
        },
        "missing_evidence": missing,
        "evidence_scope": "Somente card de busca; campos ausentes permanecem não verificados.",
    }


@app.get("/api/v1/incomplete-discoveries")
def get_incomplete_discoveries(
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    status_filter: Literal["all", "not_requested", "pending", "running", "completed", "fallback", "failed"] = "all",
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    run_id = latest_incomplete_run_id(db, profile.id)
    if not run_id:
        return {"items": [], "summary": {"total": 0}, "page": page, "page_size": page_size, "pages": 1}
    query = _incomplete_cards_query(db, profile_id=profile.id).filter(ListingDiscovery.pipeline_run_id == run_id)
    if status_filter != "all":
        query = query.filter(ListingDiscovery.card_analysis_status == status_filter)
    total = query.count()
    discoveries = query.order_by(
        ListingDiscovery.priority_score.desc(),
        ListingDiscovery.first_observed_at.desc(),
    ).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [_incomplete_discovery_public(discovery) for discovery in discoveries],
        "summary": card_analysis_counts(db, profile.id, run_id),
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    }


@app.post("/api/v1/incomplete-discoveries/analyze")
def start_incomplete_discovery_analysis(
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    result = queue_latest_incomplete_cards(profile.id)
    if not result["run_id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "INCOMPLETE_DISCOVERIES_NOT_FOUND", "message": "Não há cards incompletos disponíveis para análise."},
        )
    return {
        "message": "Análise dos cards incompletos enfileirada.",
        "queued": result["queued"],
        "total": result["total"],
    }


class PipelineChatMessageRequest(BaseModel):
    client_request_id: str
    action: Literal[
        "overview",
        "next_batch",
        "explain_criteria",
        "opportunity_summary",
        "rejection_summary",
        "price_conditions",
        "market_check",
        "ask",
    ]
    text: Optional[str] = None
    product_id: Optional[str] = None


def _pipeline_chat_exception(exc: PipelineChatError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


@app.get("/api/v1/pipelines/{id}/chat")
def get_pipeline_chat(
    id: str,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    if not db.query(PipelineRun.id).filter(PipelineRun.id == id, PipelineRun.profile_id == profile.id).first():
        raise _resource_not_found("Pipeline run")
    try:
        return PipelineChatService(db).get_chat(id)
    except PipelineChatError as exc:
        raise _pipeline_chat_exception(exc) from exc


@app.post("/api/v1/pipelines/{id}/chat/messages")
async def post_pipeline_chat_message(
    id: str,
    payload: PipelineChatMessageRequest,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    if not db.query(PipelineRun.id).filter(PipelineRun.id == id, PipelineRun.profile_id == profile.id).first():
        raise _resource_not_found("Pipeline run")
    try:
        return await PipelineChatService(db).send_message(
            id,
            client_request_id=payload.client_request_id,
            action=payload.action,
            text=payload.text,
            product_id=payload.product_id,
        )
    except PipelineChatError as exc:
        raise _pipeline_chat_exception(exc) from exc

def _pipeline_workload_public(run: PipelineRun) -> Optional[dict]:
    """Expose operational progress without leaking task or listing identifiers."""
    if run.workload_mode != "high_volume":
        return None
    state = run.workload_state or {}
    keys = (
        "mode", "phase", "discovery_target", "observed", "discovered",
        "cards_published", "cards_rejected", "cards_withheld", "cards_approved",
        "rejected_no_price", "rejected_scope", "rejected_prefilter",
        "agent_eligible", "agent_approved", "agent_rejected",
        "card_review_mode", "agent_pending", "agent_completed", "agent_failed", "agent_estimated_cost_usd",
        "agent_calls_for_no_price", "details_for_no_price", "published_without_direct_price",
        "detail_candidates", "retrieval_pending", "retrieval_completed", "deferred_retrieval_tasks",
        "enrichment_target", "enrichment_cap", "enrichment_planned",
        "enrichment_pending", "enrichment_completed", "enrichment_failed", "navigation_cap",
        "discovery_navigation_cap", "navigation_reserve", "navigations_consumed", "pace_seconds",
        "duration_minutes", "aggressiveness", "estimated_discoveries", "estimated_details_max", "estimate_basis",
        "detail_access_mode", "enrichment_threshold", "preliminary_opportunities", "confirmed_opportunities", "withdrawn_opportunities",
        "deadline_at", "not_before", "message", "parser_circuit_open", "circuit_trip_reason",
    )
    res = {key: state.get(key) for key in keys if key in state}
    pending_details = state.get("enrichment_pending")
    if pending_details is None:
        pending_details = max(
            0,
            int(state.get("enrichment_planned", 0))
            - int(state.get("enrichment_completed", 0))
            - int(state.get("enrichment_failed", 0)),
        )
    res["pending_detail_tasks"] = pending_details
    res["failed_detail_tasks"] = int(state.get("enrichment_failed", 0))
    res["attempts_count"] = len(run.attempts) if hasattr(run, "attempts") and run.attempts else 1
    res["can_resume"] = run.status in {"completed_partial", "waiting_worker", "waiting_budget", "failed"}
    return res


class PipelineRunRequest(BaseModel):
    source_type: str = "fixture"
    fixture_version: str = "v1.0.0"
    query: str = ""
    limit: int = Query(5, ge=1, le=100)
    model_mode: str = "auto"
    investigate_limit: int = Query(2, ge=0, le=10)
    search_definition_id: Optional[str] = None
    scope_ids: List[str] = []
    ad_hoc_scope: Optional[SearchScopePayload] = None
    workload_mode: Literal["standard", "high_volume"] = "standard"
    discovery_target: int = Field(default=2000, ge=1, le=2000)
    duration_minutes: int = Field(default=180, ge=5, le=180)
    aggressiveness: Literal["conservative", "balanced", "intensive"] = "balanced"
    detail_access_mode: Literal["disabled", "auto_threshold"] = "disabled"
    card_review_mode: Literal["disabled", "required"] = "disabled"
    enrichment_threshold: int = Field(default=70, ge=0, le=100)
    use_dataset_match: Optional[bool] = None
    require_price: Optional[bool] = None
    olx_pay_only: Optional[bool] = None
    delivery_only: Optional[bool] = None
    catalog_match: Optional[str] = None


class HighVolumePreflightRequest(BaseModel):
    source_type: Literal["fixture", "playwright_fixture", "olx"] = "fixture"
    query: str = ""
    limit: int = Field(default=5, ge=1, le=100)
    search_definition_id: Optional[str] = None
    scope_ids: List[str] = []
    ad_hoc_scope: Optional[SearchScopePayload] = None
    discovery_target: int = Field(default=2000, ge=1, le=2000)
    duration_minutes: int = Field(default=180, ge=5, le=180)
    aggressiveness: Literal["conservative", "balanced", "intensive"] = "balanced"
    detail_access_mode: Literal["disabled", "auto_threshold"] = "disabled"
    card_review_mode: Literal["disabled", "required"] = "disabled"
    enrichment_threshold: int = Field(default=70, ge=0, le=100)
    use_dataset_match: Optional[bool] = None
    require_price: Optional[bool] = None
    olx_pay_only: Optional[bool] = None
    delivery_only: Optional[bool] = None
    catalog_match: Optional[str] = None


def _card_agent_available() -> bool:
    configured = bool(os.getenv("VERTEX_PROJECT_ID") or os.getenv("GEMINI_API_KEY"))
    enabled = os.getenv("EXTERNAL_PROVIDERS", "disabled").lower() == "enabled"
    return enabled and configured and isinstance(get_model_gateway(), VertexModelGateway)


def _high_volume_scope_key(payload: Any) -> str:
    """Stable, internal-only scope identity for estimate history."""
    definition_id = getattr(payload, "search_definition_id", None)
    if definition_id:
        return f"definition:{definition_id}"
    scope_ids = sorted(str(value) for value in (getattr(payload, "scope_ids", None) or []))
    if scope_ids:
        return f"scopes:{','.join(scope_ids)}"
    ad_hoc = getattr(payload, "ad_hoc_scope", None)
    if ad_hoc is not None:
        data = ad_hoc.model_dump()
        return "adhoc:" + json.dumps({
            "query": str(data.get("query") or "").strip().casefold(),
            "category": data.get("category"),
            "min_price": data.get("min_price"),
            "max_price": data.get("max_price"),
            "sort": data.get("sort"),
        }, sort_keys=True, ensure_ascii=False)
    return "adhoc:" + json.dumps({
        "query": str(getattr(payload, "query", "") or "").strip().casefold(),
        "category": None,
        "min_price": None,
        "max_price": None,
        "sort": "recent",
    }, sort_keys=True, ensure_ascii=False)


def _run_high_volume_scope_key(run: PipelineRun) -> str:
    configuration = ((run.steps or [{}])[0]).get("configuration", {})
    if configuration.get("search_definition_id"):
        return f"definition:{configuration['search_definition_id']}"
    scope_ids = sorted(str(value) for value in (configuration.get("scope_ids") or []))
    if scope_ids:
        return f"scopes:{','.join(scope_ids)}"
    scopes = configuration.get("scopes") or []
    first_scope = scopes[0] if scopes else {}
    if len(scopes) == 1 and first_scope.get("id") is None:
        return "adhoc:" + json.dumps({
            "query": str(first_scope.get("query") or "").strip().casefold(),
            "category": first_scope.get("category"),
            "min_price": first_scope.get("min_price"),
            "max_price": first_scope.get("max_price"),
            "sort": first_scope.get("sort"),
        }, sort_keys=True, ensure_ascii=False)
    return "adhoc:" + json.dumps({
        "query": str(configuration.get("query") or "").strip().casefold(),
        "category": None,
        "min_price": None,
        "max_price": None,
        "sort": "recent",
    }, sort_keys=True, ensure_ascii=False)


def _validate_high_volume_scope(payload: Any, profile: Profile, db: Session) -> None:
    if payload.search_definition_id and (payload.scope_ids or payload.ad_hoc_scope is not None):
        raise HTTPException(
            status_code=422,
            detail={"code": "PIPELINE_SCOPE_MODE_CONFLICT", "message": "Use uma definição salva, escopos manuais ou uma busca avulsa por vez."},
        )
    if payload.search_definition_id:
        _require_owned_definition(db, profile, payload.search_definition_id)
        return
    if payload.scope_ids:
        selected_count = db.query(SearchScope.id).filter(
            SearchScope.id.in_(payload.scope_ids),
            SearchScope.profile_id == profile.id,
            SearchScope.marketplace == payload.source_type,
            SearchScope.enabled.is_(True),
        ).count()
        if selected_count != len(set(payload.scope_ids)):
            raise _resource_not_found("Search scope")
        return
    if payload.ad_hoc_scope is not None:
        if payload.source_type == "olx" and payload.ad_hoc_scope.marketplace != "olx":
            raise HTTPException(status_code=422, detail="O escopo avulso precisa usar marketplace=olx")
        return
    if not payload.query.strip():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "HIGH_VOLUME_QUERY_REQUIRED",
                "message": "Informe uma consulta, escopo salvo ou definição para delimitar a coleta de alto volume.",
            },
        )


def _high_volume_estimate(db: Session, profile: Profile, payload: Any, plan: dict[str, Any]) -> dict[str, Any]:
    """Estimate deduplicated discovery cards without exposing run or scope IDs."""
    since = _utcnow() - datetime.timedelta(days=90)
    scope_key = _high_volume_scope_key(payload)
    recent_runs = (
        db.query(PipelineRun)
        .filter(
            PipelineRun.profile_id == profile.id,
            PipelineRun.workload_mode == "high_volume",
            PipelineRun.started_at >= since,
        )
        .order_by(PipelineRun.started_at.desc())
        .limit(40)
        .all()
    )
    matching_runs = [
        run for run in recent_runs
        if ((run.steps or [{}])[0]).get("configuration", {}).get("source_type") == payload.source_type
        and _run_high_volume_scope_key(run) == scope_key
    ]

    def yields_for(runs: list[PipelineRun]) -> list[int]:
        if not runs:
            return []
        rows = db.query(PipelineRetrievalTask.discovered_count).filter(
            PipelineRetrievalTask.pipeline_run_id.in_([run.id for run in runs]),
            PipelineRetrievalTask.status == "completed",
        ).all()
        return [max(0, int(row[0] or 0)) for row in rows]

    yields = yields_for(matching_runs)
    basis = "histórico deste escopo"
    if len(yields) < 3:
        source_runs = [
            run for run in recent_runs
            if ((run.steps or [{}])[0]).get("configuration", {}).get("source_type") == payload.source_type
        ]
        yields = yields_for(source_runs)
        basis = "histórico OLX deste perfil" if payload.source_type == "olx" else "histórico local deste perfil"
    if len(yields) < 3:
        lower_yield, upper_yield = 30.0, 45.0
        basis = "estimativa conservadora por página"
    else:
        lower_yield, upper_yield = np.percentile(yields, [25, 75]).tolist()
        lower_yield = min(50.0, max(0.0, lower_yield))
        upper_yield = min(50.0, max(lower_yield, upper_yield))

    pages = int(plan["max_discovery_pages"])
    target = int(plan["discovery_target"])
    estimate_min = min(target, max(0, int(round(lower_yield * pages))))
    estimate_max = min(target, max(estimate_min, int(round(upper_yield * pages))))
    estimate_runs = matching_runs or [
        run for run in recent_runs
        if ((run.steps or [{}])[0]).get("configuration", {}).get("source_type") == payload.source_type
    ]
    eligibility_rate = 0.65
    if estimate_runs:
        ids = [run.id for run in estimate_runs]
        observed = db.query(func.count(ListingDiscovery.id)).filter(
            ListingDiscovery.pipeline_run_id.in_(ids)
        ).scalar() or 0
        eligible = db.query(func.count(ListingDiscovery.id)).filter(
            ListingDiscovery.pipeline_run_id.in_(ids),
            ListingDiscovery.gate_status == "agent_eligible",
        ).scalar() or 0
        # Legacy runs have no durable gate outcome.  Their direct-price rate
        # is still safer than charging the model for every discovered card.
        if observed and eligible:
            eligibility_rate = max(0.0, min(1.0, float(eligible) / float(observed)))
            basis += "; filtros de card observados"
        elif observed:
            direct = db.query(func.count(ListingDiscovery.id)).filter(
                ListingDiscovery.pipeline_run_id.in_(ids),
                ListingDiscovery.price.is_not(None), ListingDiscovery.price > 0,
                ListingDiscovery.price_origin.in_(("structured", "dom")),
            ).scalar() or 0
            eligibility_rate = max(0.0, min(1.0, float(direct) / float(observed)))
            basis += "; preço direto histórico"
    reviews_min = int(round(estimate_min * eligibility_rate))
    reviews_max = int(round(estimate_max * eligibility_rate))
    return {
        "estimated_discoveries": {"min": estimate_min, "max": estimate_max},
        "estimate_basis": basis,
        "estimated_agent_reviews": {"min": reviews_min, "max": reviews_max},
        "estimated_prefilter_eligible_rate": round(eligibility_rate, 3),
        "estimated_details_max": min(100, max(0, int(plan["navigation_cap"]) - pages)),
        "retrieval_pages": pages,
    }


@app.post("/api/v1/pipelines/high-volume/preflight")
async def high_volume_preflight(
    req: HighVolumePreflightRequest,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    """Return a start forecast without reserving capacity or queuing work."""
    profile = _require_profile(db, x_profile_id)
    _validate_high_volume_scope(req, profile, db)
    from packages.pipeline.high_volume import build_high_volume_plan

    plan = build_high_volume_plan(
        duration_minutes=req.duration_minutes,
        aggressiveness=req.aggressiveness,
        discovery_target=req.discovery_target,
        detail_access_mode=req.detail_access_mode,
        enrichment_threshold=req.enrichment_threshold,
    )
    plan["card_review_mode"] = req.card_review_mode
    estimate = _high_volume_estimate(db, profile, req, plan)
    forecast = {
        "mode": "high_volume",
        **plan,
        **estimate,
        "estimated_duration_seconds": req.duration_minutes * 60,
        "required_to_start": 0,
        "can_start": True,
        "state": "ready",
        "card_agent_available": _card_agent_available(),
        "estimated_agent_reviews_max": int((estimate.get("estimated_agent_reviews") or {}).get("max", 0)) if req.card_review_mode == "required" else 0,
    }
    if req.card_review_mode == "required" and not forecast["card_agent_available"]:
        forecast.update({
            "can_start": False,
            "state": "unavailable",
            "message": "Ative o provedor de modelos para revisar cards com agente.",
        })
    if req.detail_access_mode == "disabled":
        forecast["estimated_details_max"] = 0
    if req.source_type != "olx" or not forecast["can_start"]:
        return forecast
    if os.getenv("APP_MODE", "local").lower() != "live":
        forecast.update({"can_start": False, "state": "unavailable", "message": "OLX requer modo live."})
        return forecast
    try:
        capacity = await _browser_worker_request("GET", "/rate-limit")
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        forecast.update({"can_start": False, "state": "unavailable", "message": detail.get("message", "Disponibilidade OLX indisponível.")})
        return forecast
    required = 2  # one forced session verification and the first retrieval page
    remaining = int(capacity.get("remaining", 0))
    cooling_down = capacity.get("state") == "cooldown"
    forecast.update({
        "required_to_start": required,
        "remaining": remaining,
        "reset_at": capacity.get("reset_at"),
        "retry_after_seconds": capacity.get("retry_after_seconds"),
        "can_start": not cooling_down and remaining >= required,
        "state": "waiting_budget" if cooling_down or remaining < required else "ready",
    })
    if not forecast["can_start"]:
        forecast["message"] = "Aguarde capacidade OLX para iniciar a primeira página."
    return forecast

@app.post("/api/v1/pipelines/run", status_code=status.HTTP_202_ACCEPTED)
async def run_pipeline(
    req: PipelineRunRequest,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    if req.source_type not in {"fixture", "olx", "playwright_fixture"}:
        raise HTTPException(status_code=422, detail="source_type must be fixture, playwright_fixture, or olx")
    if req.source_type == "olx" and os.getenv("APP_MODE", "local").lower() != "live":
        raise HTTPException(status_code=409, detail="OLX is opt-in and requires APP_MODE=live")
    if req.workload_mode == "high_volume":
        _validate_high_volume_scope(req, profile, db)
    elif req.card_review_mode != "disabled":
        raise HTTPException(status_code=422, detail={
            "code": "CARD_REVIEW_HIGH_VOLUME_ONLY",
            "message": "A revisão de cards por agente está disponível apenas no alto volume.",
        })
    if req.card_review_mode == "required" and not _card_agent_available():
        raise HTTPException(status_code=409, detail={
            "code": "CARD_AGENT_UNAVAILABLE",
            "message": "Ative o provedor de modelos antes de executar a revisão de cards.",
        })
    scopes: list[dict] = []
    definition: Optional[SearchDefinition] = None
    if req.search_definition_id:
        if req.scope_ids or req.ad_hoc_scope is not None:
            raise HTTPException(
                status_code=422,
                detail={"code": "PIPELINE_SCOPE_MODE_CONFLICT", "message": "Use uma definição salva, escopos manuais ou uma busca avulsa por vez."},
            )
        definition = _require_owned_definition(db, profile, req.search_definition_id)
        try:
            from packages.search import prepare_pipeline_scopes

            scopes = prepare_pipeline_scopes(definition.plan or {}, marketplace=req.source_type)
        except (ImportError, AttributeError, TypeError, ValueError):
            intent = definition.intent or {}
            plan = definition.plan or {}
            query = str(plan.get("query") or intent.get("query") or intent.get("raw_query") or definition.name)
            scopes = [{
                "id": None,
                "name": definition.name,
                "marketplace": req.source_type,
                "query": query,
                "category": intent.get("category"),
                "min_price": (intent.get("budget") or {}).get("min"),
                "max_price": (intent.get("budget") or {}).get("max"),
                "sort": "recent",
                "limit": max(1, min(int(plan.get("desired_count") or intent.get("desired_count") or req.limit), 30)),
                "enabled": True,
                "plan": plan,
            }]
        for scope in scopes:
            scope["name"] = definition.name if len(scopes) == 1 else f"{definition.name} · {scope.get('name') or 'consulta'}"
            scope["search_definition_id"] = definition.id
            scope["search_definition_name"] = definition.name
        if any(scope.get("catalog_match") for scope in scopes) and (
            req.workload_mode != "high_volume" or str((definition.plan or {}).get("scope_mode") or "precise") == "precise"
        ):
            try:
                from packages.catalog import ensure_notebook_catalog

                catalog = ensure_notebook_catalog(db)
                for scope in scopes:
                    if scope.get("catalog_match"):
                        scope["catalog_products"] = catalog["products"]
            except FileNotFoundError as exc:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "NOTEBOOK_CATALOG_UNAVAILABLE",
                        "message": "O catálogo de notebooks não está disponível para esta execução.",
                    },
                ) from exc
    elif req.scope_ids:
        selected = db.query(SearchScope).filter(
            SearchScope.id.in_(req.scope_ids),
            SearchScope.profile_id == profile.id,
            SearchScope.marketplace == req.source_type,
            SearchScope.enabled.is_(True),
        ).all()
        selected_by_id = {scope.id: scope for scope in selected}
        missing = [scope_id for scope_id in req.scope_ids if scope_id not in selected_by_id]
        if missing:
            raise _resource_not_found("Search scope")
        scopes = [{
            **_scope_to_dict(selected_by_id[scope_id]),
            "id": scope_id,
            "collection_mode": req.workload_mode == "high_volume",
        } for scope_id in req.scope_ids]
    elif req.ad_hoc_scope is not None:
        if req.source_type == "olx" and req.ad_hoc_scope.marketplace != "olx":
            raise HTTPException(status_code=422, detail="O escopo avulso precisa usar marketplace=olx")
        scopes = [{
            "id": None,
            **req.ad_hoc_scope.model_dump(),
            "collection_mode": req.workload_mode == "high_volume",
            "scope_mode": "broad" if req.workload_mode == "high_volume" else "precise",
        }]
    elif req.source_type == "olx":
        if not req.query.strip():
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "search_scope_required",
                    "message": "Selecione ao menos um escopo salvo ou informe uma busca avulsa.",
                },
            )
        scopes = [{
            "id": None,
            "name": "Busca avulsa",
            "marketplace": "olx",
            "query": req.query,
            "category": None,
            "min_price": None,
            "max_price": None,
            "sort": "recent",
            "limit": req.limit,
            "enabled": True,
            "collection_mode": req.workload_mode == "high_volume",
        }]
    else:
        # Fixture jobs remain compatible with the original API contract.
        scopes = [{
            "id": None,
            "name": "Coleta ampla" if req.workload_mode == "high_volume" else "Fixture",
            "marketplace": req.source_type,
            "query": req.query,
            "category": None,
            "min_price": None,
            "max_price": None,
            "sort": "recent",
            "limit": req.limit,
            "enabled": True,
            "collection_mode": req.workload_mode == "high_volume",
        }]
    if req.workload_mode == "high_volume":
        # High volume controls execution scale, not search semantics. Precise
        # definitions keep their match requirements; only explicitly broad
        # scopes collect inventory for later filtering.
        for scope in scopes:
            scope_mode = str(scope.get("scope_mode") or ((scope.get("plan") or {}).get("scope_mode")) or "broad")
            scope["scope_mode"] = scope_mode
            scope["collection_mode"] = scope_mode == "broad"
            if scope_mode == "broad":
                scope["product_id"] = None
                scope.pop("catalog_match", None)
                scope.pop("catalog_products", None)
    for scope in scopes:
        if req.require_price is not None:
            scope["require_price"] = req.require_price
        if req.olx_pay_only is not None:
            scope["olx_pay_only"] = req.olx_pay_only
        if req.delivery_only is not None:
            scope["delivery_only"] = req.delivery_only
    if req.source_type == "olx":
        # Worst case: one forced session validation plus one search and one
        # detail navigation for each requested item in every scope.
        navigation_budget = (
            2 if req.workload_mode == "high_volume"
            else 1 + sum(1 + int(scope.get("limit", req.limit)) for scope in scopes)
        )
        try:
            await _browser_worker_request("POST", "/rate-limit/preflight", {"needed": navigation_budget})
            browser_status = await _browser_worker_request("GET", "/session/status?force=true")
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            if detail.get("code") in {"olx_budget_exhausted", "olx_circuit_open", "olx_access_blocked"}:
                raise
            if exc.status_code != 503:
                raise
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "olx_session_unavailable",
                    "message": detail.get("message", "O navegador da OLX está indisponível."),
                },
            ) from exc
        if not browser_status.get("authenticated"):
            session_state = browser_status.get("session_state", "disconnected")
            unavailable = session_state == "unavailable"
            error_code = browser_status.get("error_code")
            if error_code in {"olx_budget_exhausted", "olx_circuit_open", "olx_access_blocked"}:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": error_code,
                        "message": browser_status.get("error_message") or "A proteção OLX bloqueou a operação.",
                        "retry_after_seconds": browser_status.get("retry_after_seconds"),
                        "reset_at": browser_status.get("reset_at"),
                        "remaining": browser_status.get("remaining"),
                    },
                )
            raise HTTPException(
                status_code=503 if unavailable else 409,
                detail={
                    "code": "olx_session_unavailable" if unavailable else "olx_session_required",
                    "message": browser_status.get("error_message")
                    or "Faça login e confirme a sessão OLX antes de iniciar o pipeline.",
                },
            )
    high_volume_plan: dict[str, Any] | None = None
    high_volume_forecast: dict[str, Any] | None = None
    workload_deadline = None
    if req.workload_mode == "high_volume":
        from packages.pipeline.high_volume import build_high_volume_plan

        high_volume_plan = build_high_volume_plan(
            duration_minutes=req.duration_minutes,
            aggressiveness=req.aggressiveness,
            discovery_target=req.discovery_target,
            detail_access_mode=req.detail_access_mode,
            enrichment_threshold=req.enrichment_threshold,
        )
        high_volume_plan["card_review_mode"] = req.card_review_mode
        high_volume_forecast = _high_volume_estimate(db, profile, req, high_volume_plan)
        workload_deadline = _utcnow() + datetime.timedelta(minutes=req.duration_minutes)

    run_id = f"pipe-{uuid.uuid4().hex[:8]}"
    run = PipelineRun(
        id=run_id,
        profile_id=profile.id,
        type=f"{req.source_type}_{'high_volume_' if req.workload_mode == 'high_volume' else ''}ingest",
        status="pending",
        started_at=_utcnow(),
        workload_mode=req.workload_mode,
        workload_deadline=workload_deadline,
        workload_state=(
            {
                "mode": "high_volume",
                "phase": "queued",
                **high_volume_plan,
                **high_volume_forecast,
                "deadline_at": workload_deadline.isoformat() if workload_deadline else None,
            }
            if req.workload_mode == "high_volume" else {}
        ),
        steps=[{
            "configuration": {
                "source_type": req.source_type,
                "fixture_version": req.fixture_version,
                "query": req.query,
                "limit": req.limit,
                "model_mode": req.model_mode,
                "investigate_limit": req.investigate_limit,
                "search_definition_id": definition.id if definition else None,
                "search_definition_name": definition.name if definition else None,
                "scope_ids": req.scope_ids,
                "scopes": scopes,
                "workload_mode": req.workload_mode,
                "high_volume": {
                    **high_volume_plan,
                    "forecast": high_volume_forecast,
                } if req.workload_mode == "high_volume" else None,
            }
        }],
    )
    db.add(run)
    db.commit()
    return {
        "pipeline_id": run_id,
        "status": "pending",
        "message": "Pipeline de alto volume enfileirado." if req.workload_mode == "high_volume" else "Pipeline persisted and queued for the worker.",
    }


class ResumePipelineRequest(BaseModel):
    include_failed: bool = False


class PipelineEnrichmentRequest(BaseModel):
    threshold: float = Field(default=70, ge=0, le=100)
    max_items: int = Field(default=100, ge=1, le=100)
    min_price: Optional[float] = Field(default=None, ge=0)
    max_price: Optional[float] = Field(default=None, ge=0)
    category: Optional[str] = None
    location: Optional[str] = None


@app.post("/api/v1/pipelines/{id}/enrichment")
async def enrich_pipeline_signals(
    id: str,
    payload: PipelineEnrichmentRequest,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    run = db.query(PipelineRun).filter(PipelineRun.id == id, PipelineRun.profile_id == profile.id).first()
    if not run or run.workload_mode != "high_volume":
        raise _resource_not_found("High-volume pipeline")
    configuration = ((run.steps or [{}])[0]).get("configuration", {})
    if configuration.get("source_type") == "olx":
        capacity = await _browser_worker_request("GET", "/rate-limit")
        remaining = int(capacity.get("remaining", 0))
        if capacity.get("state") == "cooldown" or remaining < 1:
            raise HTTPException(status_code=429, detail={
                "code": "olx_budget_exhausted",
                "message": "Aguarde capacidade OLX para enriquecer os candidatos selecionados.",
                "retry_after_seconds": capacity.get("retry_after_seconds"),
                "remaining": remaining,
            })
        payload.max_items = min(payload.max_items, remaining)
    from packages.pipeline.high_volume import schedule_signal_enrichment

    return schedule_signal_enrichment(id, **payload.model_dump(), actor="operator")


@app.post("/api/v1/pipelines/{id}/resume")
async def resume_pipeline(
    id: str,
    payload: ResumePipelineRequest = ResumePipelineRequest(),
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    run = db.query(PipelineRun).filter(
        PipelineRun.id == id,
        PipelineRun.profile_id == profile.id,
    ).first()
    if not run:
        raise _resource_not_found("Pipeline run")
    if run.workload_mode != "high_volume":
        raise HTTPException(status_code=422, detail="Apenas pipelines de alto volume podem ser retomados.")

    config = ((run.steps or [{}])[0]).get("configuration", {})
    if config.get("source_type") == "olx":
        try:
            await _browser_worker_request("POST", "/rate-limit/preflight", {"needed": 2})
            browser_status = await _browser_worker_request("GET", "/session/status?force=true")
            if not browser_status.get("authenticated"):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "olx_session_required",
                        "message": "Faça login e confirme a sessão OLX antes de retomar os detalhes.",
                    },
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "olx_session_unavailable", "message": f"Navegador OLX indisponível: {exc}"},
            ) from exc

    from packages.pipeline.high_volume import resume_high_volume_run
    result = resume_high_volume_run(id, include_failed=payload.include_failed, actor="operator")
    return {
        "status": result.get("status", "queued"),
        "message": "Enriquecimento de detalhes pendentes retomado com sucesso.",
        "workload": result,
    }


@app.post("/api/v1/pipelines/{id}/reanalyze")
def reanalyze_pipeline(
    id: str,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    run = db.query(PipelineRun).filter(
        PipelineRun.id == id,
        PipelineRun.profile_id == profile.id,
    ).first()
    if not run:
        raise _resource_not_found("Pipeline run")
    if run.workload_mode != "high_volume":
        raise HTTPException(status_code=422, detail="Apenas pipelines de alto volume suportam reanálise local.")

    from packages.pipeline.high_volume import reanalyze_high_volume_run
    result = reanalyze_high_volume_run(id, db=db)
    return {
        "status": result.get("status", run.status),
        "message": "Reanálise local do dataset e consolidação de mercado concluída.",
        "workload": result,
    }


@app.post("/api/v1/pipelines/{id}/agent-review/retry")
def retry_pipeline_agent_review(
    id: str,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    run = db.query(PipelineRun).filter(PipelineRun.id == id, PipelineRun.profile_id == profile.id).first()
    if not run:
        raise _resource_not_found("Pipeline run")
    configuration = ((run.steps or [{}])[0]).get("configuration", {})
    if (configuration.get("high_volume") or {}).get("card_review_mode") != "required":
        raise HTTPException(status_code=422, detail={
            "code": "CARD_REVIEW_NOT_ENABLED",
            "message": "Esta execução não usa revisão obrigatória de cards.",
        })
    if not _card_agent_available():
        raise HTTPException(status_code=409, detail={
            "code": "CARD_AGENT_UNAVAILABLE",
            "message": "Ative o provedor de modelos antes de tentar novamente.",
        })
    result = retry_failed_run_cards(id)
    return {"status": "reviewing_cards" if result["queued"] else run.status, "queued": result["queued"]}


@app.post("/api/v1/pipelines/{id}/cancel")
@app.post("/api/v1/pipelines/{id}/stop")
def cancel_pipeline(
    id: str,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    run = db.query(PipelineRun).filter(
        PipelineRun.id == id,
        PipelineRun.profile_id == profile.id,
    ).first()
    if not run:
        raise _resource_not_found("Pipeline run")

    from packages.pipeline.runner import cancel_pipeline_run
    return cancel_pipeline_run(id, actor="operator")



# ----------------------------------------------------------------------
# 7. Retrieval (Product Knowledge)
# ----------------------------------------------------------------------

class SearchRequest(BaseModel):
    query: str
    category: Optional[str] = None
    limit: int = 5

@app.post("/api/v1/retrieval/search")
async def search_knowledge(req: SearchRequest, db: Session = Depends(get_db)):
    retriever = get_retriever()
    results = await retriever.search(
        query=req.query,
        category=req.category,
        limit=req.limit,
        db=db
    )
    return {
        "query": req.query,
        "results": [r.model_dump() for r in results]
    }

# ----------------------------------------------------------------------
# 8. Benchmarks and Evaluation
# ----------------------------------------------------------------------

@app.get("/api/v1/benchmarks")
def get_benchmarks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    query = db.query(EvalRun)
    total = query.count()
    runs = query.order_by(desc(EvalRun.executed_at)).offset((page - 1) * page_size).limit(page_size).all()

    items = [
        {
            "id": e.id,
            "dataset_version": e.dataset_version,
            "executed_at": e.executed_at.isoformat() if e.executed_at else None,
            "configuration": e.configuration,
            "metrics": e.metrics,
            "details": e.details,
            "failures_count": e.failures_count,
            "status": e.status
        }
        for e in runs
    ]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size)
    }

@app.get("/api/v1/benchmarks/{id}")
def get_benchmark(id: str, db: Session = Depends(get_db)):
    e = db.query(EvalRun).filter(EvalRun.id == id).first()
    if not e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "RESOURCE_NOT_FOUND", "message": f"Benchmark run {id} not found", "details": None}}
        )
    return {
        "id": e.id,
        "dataset_version": e.dataset_version,
        "executed_at": e.executed_at.isoformat() if e.executed_at else None,
        "configuration": e.configuration,
        "metrics": e.metrics,
        "failures_count": e.failures_count,
        "status": e.status,
        "details": e.details
    }

class BenchmarkRunRequest(BaseModel):
    dataset_version: str = "v1.0.0"

@app.post("/api/v1/benchmarks/run", status_code=status.HTTP_202_ACCEPTED)
def run_benchmark(req: BenchmarkRunRequest, db: Session = Depends(get_db)):
    run_id = f"eval-run-{uuid.uuid4().hex[:6]}"
    run = EvalRun(
        id=run_id,
        dataset_version=req.dataset_version,
        configuration="deterministic_local_v1",
        executed_at=_utcnow(),
        status="pending",
        failures_count=0,
        metrics={},
        details={}
    )
    db.add(run)
    db.commit()
    return {
        "benchmark_id": run_id,
        "status": "pending",
        "message": "Benchmark suite started."
    }

# ----------------------------------------------------------------------
# 9. Settings and User Preferences
# ----------------------------------------------------------------------

@app.get("/api/v1/settings")
def get_settings(
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    pref = db.query(UserPreference).filter(UserPreference.profile_id == profile.id).first()
    mg = get_model_gateway()
    ep = get_embedding_provider()
    ret = get_retriever()
    is_vertex = bool(os.getenv("VERTEX_PROJECT_ID") or os.getenv("GEMINI_API_KEY"))

    app_mode = os.getenv("APP_MODE", "local").lower()
    marketplace_source = os.getenv("MARKETPLACE_SOURCE", "fixture").lower()
    ext_enabled = os.getenv("EXTERNAL_PROVIDERS", "disabled").lower() == "enabled"
    olx_active = app_mode == "live" and ext_enabled and marketplace_source == "olx"

    return {
        "app_mode": app_mode,
        "synthetic_data": app_mode == "local",
        "fixture_version": "v1.0.0",
        "external_providers": {
            "olx": "active" if olx_active else "disabled",
            "vertex_ai": "active" if (isinstance(mg, VertexModelGateway) and is_vertex and ext_enabled) else "disabled",
            "vertex_search": "active" if (isinstance(ret, VertexSearchRetriever) and is_vertex and ext_enabled) else "disabled",
            "gemini_embeddings": "active" if (isinstance(ep, GeminiEmbeddingProvider) and is_vertex and ext_enabled) else "disabled"
        },
        "active_adapters": {
            "marketplace": "OlxSource" if olx_active else "FixtureMarketplaceSource",
            "model": mg.__class__.__name__,
            "model_auth_mode": configured_genai_auth_mode(),
            "embedding": ep.__class__.__name__,
            "retriever": ret.__class__.__name__
        },
        "profile": _profile_dict(profile),
        "preference_profile": profile.preferences if profile.preferences is not None else (pref.preferences if pref else {})
    }

@app.put("/api/v1/settings")
def update_settings(
    payload: dict,
    x_profile_id: Optional[str] = Header(default=None, alias="X-Profile-ID"),
    db: Session = Depends(get_db),
):
    profile = _require_profile(db, x_profile_id)
    pref = db.query(UserPreference).filter(UserPreference.profile_id == profile.id).first()
    if not pref:
        pref = UserPreference(profile_id=profile.id, preferences={})
        db.add(pref)
    
    new_prefs = payload.get("preference_profile", payload)
    pref.preferences = new_prefs
    profile.preferences = new_prefs
    profile.updated_at = _utcnow()
    db.commit()
    return get_settings(x_profile_id=x_profile_id, db=db)

# ----------------------------------------------------------------------
# 10. Vertex AI Diagnostic & Inference Test Endpoints
# ----------------------------------------------------------------------

class VertexTestNormalizeRequest(BaseModel):
    title: str
    description: str = ""
    category_hint: Optional[str] = "gpu"

@app.post("/api/v1/vertex/test-normalize")
async def test_vertex_normalize(req: VertexTestNormalizeRequest):
    mg = get_model_gateway()
    try:
        with model_metric_context(origin="api", operation="diagnostics"):
            res = await mg.normalize_listing(
                title=req.title,
                description=req.description,
                category_hint=req.category_hint
            )
        return {
            "status": "success",
            "adapter": mg.__class__.__name__,
            "result": res.model_dump()
        }
    except Exception as e:
        return {
            "status": "error",
            "adapter": mg.__class__.__name__,
            "error": str(e)
        }


# ----------------------------------------------------------------------
# 11. Persisted model metrics
# ----------------------------------------------------------------------

_METRIC_WINDOWS = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}


def _model_metric_query(db: Session, window: str, model_id: Optional[str], operation: Optional[str], status_filter: Optional[str] = None, pipeline_run_id: Optional[str] = None):
    if window not in {*_METRIC_WINDOWS, "all"}:
        raise HTTPException(status_code=422, detail="window must be 24h, 7d, 30d, or all")
    now = _utcnow()
    from_value = now - datetime.timedelta(hours=_METRIC_WINDOWS[window]) if window != "all" else None
    query = db.query(ModelMetricCall)
    if from_value:
        query = query.filter(ModelMetricCall.started_at >= from_value)
    if model_id:
        query = query.filter(ModelMetricCall.model_id == model_id)
    if operation:
        query = query.filter(ModelMetricCall.operation == operation)
    if status_filter:
        query = query.filter(ModelMetricCall.status == status_filter)
    if pipeline_run_id:
        query = query.filter(ModelMetricCall.pipeline_run_id == pipeline_run_id)
    return query, from_value, now


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower, upper = int(index), min(int(index) + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower), 3)


@app.get("/api/v1/model-metrics/summary")
def get_model_metrics_summary(
    window: str = "24h", model_id: Optional[str] = None, operation: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query, from_value, now = _model_metric_query(db, window, model_id, operation)
    rows = query.all()
    calls = len(rows)
    latencies = [float(row.duration_ms) for row in rows if row.duration_ms is not None]
    token_names = ("prompt_tokens", "candidates_tokens", "thoughts_tokens", "cached_tokens", "tool_tokens", "total_tokens")
    token_sums = {name: sum(int(getattr(row, name) or 0) for row in rows) for name in token_names}
    by_model = []
    for key in sorted({row.model_id for row in rows}):
        subset = [row for row in rows if row.model_id == key]
        by_model.append({
            "model_id": key, "calls": len(subset),
            "estimated_cost_usd": round(sum(float(row.estimated_cost_usd or 0) for row in subset), 12),
            "average_latency_ms": round(sum(float(row.duration_ms or 0) for row in subset) / len(subset), 3),
            "success_rate": round(sum(row.status == "success" for row in subset) / len(subset), 6),
            "tokens_total": sum(int(row.total_tokens or 0) for row in subset),
        })
    by_operation = []
    for key in sorted({row.operation for row in rows}):
        subset = [row for row in rows if row.operation == key]
        by_operation.append({
            "operation": key, "calls": len(subset),
            "estimated_cost_usd": round(sum(float(row.estimated_cost_usd or 0) for row in subset), 12),
            "average_latency_ms": round(sum(float(row.duration_ms or 0) for row in subset) / len(subset), 3),
        })
    return {
        "window": window, "from": from_value.isoformat() if from_value else None, "to": now.isoformat(),
        "currency": "USD", "estimated_cost_usd": round(sum(float(row.estimated_cost_usd or 0) for row in rows), 12),
        "calls": calls, "success_rate": round(sum(row.status == "success" for row in rows) / calls, 6) if calls else 0.0,
        "fallback_rate": round(sum(row.status == "fallback" for row in rows) / calls, 6) if calls else 0.0,
        "latency_ms": {"average": round(sum(latencies) / len(latencies), 3) if latencies else 0.0, "p50": _percentile(latencies, .50), "p95": _percentile(latencies, .95)},
        "tokens": {"prompt": token_sums["prompt_tokens"], "candidates": token_sums["candidates_tokens"], "thoughts": token_sums["thoughts_tokens"], "cached": token_sums["cached_tokens"], "tool": token_sums["tool_tokens"], "total": token_sums["total_tokens"]},
        "by_model": by_model, "by_operation": by_operation,
    }


@app.get("/api/v1/model-metrics/calls")
def get_model_metric_calls(
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100), window: str = "24h",
    model_id: Optional[str] = None, operation: Optional[str] = None, status: Optional[str] = None,
    pipeline_run_id: Optional[str] = None, db: Session = Depends(get_db),
):
    query, _, _ = _model_metric_query(db, window, model_id, operation, status, pipeline_run_id)
    total = query.count()
    rows = query.order_by(desc(ModelMetricCall.started_at)).offset((page - 1) * page_size).limit(page_size).all()
    fields = ("id", "pipeline_run_id", "origin", "operation", "provider", "auth_mode", "model_id", "status", "duration_ms", "prompt_tokens", "candidates_tokens", "thoughts_tokens", "cached_tokens", "tool_tokens", "total_tokens", "finish_reason", "estimated_cost_usd", "pricing_version", "error_code")
    return {"items": [{**{field: getattr(row, field) for field in fields}, "started_at": row.started_at.isoformat() if row.started_at else None} for row in rows], "total": total, "page": page, "page_size": page_size, "pages": max(1, (total + page_size - 1) // page_size)}


# 12. Opt-in marketplace session endpoints
# ----------------------------------------------------------------------

SESSION_CACHE_DIR = os.getenv("OLX_SESSION_DIR", ".cache/olx_session")
SESSION_FILE = os.path.join(SESSION_CACHE_DIR, "storage_state.json")
SESSION_METADATA_FILE = os.path.join(SESSION_CACHE_DIR, "session_metadata.json")


class MarketplaceAuthStartRequest(BaseModel):
    email: str


class MarketplaceAuthCodeRequest(BaseModel):
    code: str


async def _browser_worker_request(method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    import httpx

    browser_worker_url = os.getenv("BROWSER_WORKER_URL", "http://browser-worker:8100").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=75.0) as client:
            response = await client.request(method, f"{browser_worker_url}{path}", json=payload)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        try:
            body = exc.response.json()
        except ValueError:
            body = {}
        detail = body.get(
            "detail",
            {
                "code": "browser_worker_http_error",
                "message": "O browser-worker rejeitou a solicitação.",
            },
        )
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "olx_browser_worker_unavailable",
                "message": "O browser-worker da OLX está indisponível. Tente novamente.",
            },
        ) from exc


@app.get("/api/v1/marketplace/rate-limit")
async def get_marketplace_rate_limit():
    if os.getenv("APP_MODE", "local").lower() != "live":
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return {
            "state": "closed", "limit_per_hour": 60, "used": 0, "remaining": 60,
            "reset_at": now, "retry_after_seconds": 0,
            "min_interval_seconds": 8, "max_interval_seconds": 12,
        }
    return await _browser_worker_request("GET", "/rate-limit")


@app.post("/api/v1/marketplace/auth/start")
async def start_marketplace_auth(request: MarketplaceAuthStartRequest, marketplace: str = "olx"):
    if marketplace.lower() != "olx":
        raise HTTPException(status_code=422, detail="Only OLX is supported by this browser worker")
    if os.getenv("APP_MODE", "local").lower() != "live":
        raise HTTPException(status_code=409, detail="OLX login requires APP_MODE=live")
    result = await _browser_worker_request("POST", "/auth/start", {"email": request.email})
    return {
        **result,
        "marketplace": "olx",
        "read_only_scope": ["search", "listing_detail"],
        "write_actions": False,
    }


@app.post("/api/v1/marketplace/auth/verify-code")
async def verify_marketplace_code(request: MarketplaceAuthCodeRequest, marketplace: str = "olx"):
    if marketplace.lower() != "olx":
        raise HTTPException(status_code=422, detail="Only OLX is supported by this browser worker")
    if os.getenv("APP_MODE", "local").lower() != "live":
        raise HTTPException(status_code=409, detail="OLX login requires APP_MODE=live")
    result = await _browser_worker_request("POST", "/auth/verify-code", {"code": request.code})
    return {
        **result,
        "marketplace": "olx",
        "read_only_scope": ["search", "listing_detail"],
        "write_actions": False,
    }


@app.post("/api/v1/marketplace/auth/manual-start")
async def start_manual_marketplace_auth(marketplace: str = "olx"):
    if marketplace.lower() != "olx":
        raise HTTPException(status_code=422, detail="Only OLX is supported by this browser worker")
    if os.getenv("APP_MODE", "local").lower() != "live":
        raise HTTPException(status_code=409, detail="OLX login requires APP_MODE=live")
    result = await _browser_worker_request("POST", "/auth/manual-start")
    return {
        **result,
        "marketplace": "olx",
        "read_only_scope": ["search", "listing_detail"],
        "write_actions": False,
    }


@app.post("/api/v1/marketplace/auth/manual-complete")
async def complete_manual_marketplace_auth(marketplace: str = "olx"):
    if marketplace.lower() != "olx":
        raise HTTPException(status_code=422, detail="Only OLX is supported by this browser worker")
    if os.getenv("APP_MODE", "local").lower() != "live":
        raise HTTPException(status_code=409, detail="OLX login requires APP_MODE=live")
    result = await _browser_worker_request("POST", "/auth/manual-complete")
    return {
        **result,
        "marketplace": "olx",
        "read_only_scope": ["search", "listing_detail"],
        "write_actions": False,
    }


@app.get("/api/v1/marketplace/auth/status")
async def get_marketplace_auth_status(force: bool = False):
    browser_status: Dict[str, Any] = {}
    worker_error: Dict[str, Any] = {}
    metadata: Dict[str, Any] = {}
    try:
        with open(SESSION_METADATA_FILE, "r", encoding="utf-8") as metadata_file:
            loaded_metadata = json.load(metadata_file)
            metadata = loaded_metadata if isinstance(loaded_metadata, dict) else {}
    except (OSError, ValueError):
        metadata = {}

    if os.getenv("APP_MODE", "local").lower() == "live":
        try:
            browser_status = await _browser_worker_request(
                "GET", "/session/status?force=true" if force else "/session/status"
            )
        except HTTPException as exc:
            worker_error = exc.detail if isinstance(exc.detail, dict) else {}
            browser_status = {}

    local_cookies = 0
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as session_file:
            data = json.load(session_file)
        local_cookies = len(data.get("cookies", []))
    except (OSError, ValueError):
        local_cookies = 0

    email = str(browser_status.get("email") or metadata.get("email") or "")
    accounts = browser_status.get("accounts")
    if not isinstance(accounts, list):
        accounts = []
    if not accounts and email:
        accounts = [{
            "marketplace": "olx",
            "email": email,
            "active": True,
            "authenticated": False,
            "authenticated_at": metadata.get("authenticated_at"),
            "last_verified_at": metadata.get("last_verified_at"),
        }]

    live_mode = os.getenv("APP_MODE", "local").lower() == "live"
    worker_available = bool(browser_status.get("worker_available")) if browser_status else not live_mode
    session_state = browser_status.get("session_state") or ("unavailable" if live_mode else "disconnected")
    error_code = browser_status.get("error_code") or worker_error.get("code")
    error_message = browser_status.get("error_message") or worker_error.get("message", "")

    return {
        "authenticated": bool(browser_status.get("authenticated")),
        "session_state": session_state,
        "email": email,
        "accounts": accounts,
        "marketplace": "olx",
        "authenticated_at": browser_status.get("authenticated_at") or metadata.get("authenticated_at"),
        "last_verified_at": browser_status.get("last_verified_at") or metadata.get("last_verified_at"),
        "last_checked_at": browser_status.get("last_checked_at") or metadata.get("last_checked_at"),
        "expires_at": None,
        "cookies_cached": max(local_cookies, int(browser_status.get("cookies_cached", 0))),
        "probe_cache_age_seconds": browser_status.get("probe_cache_age_seconds"),
        "manual_browser_session": bool(local_cookies or email),
        "human_emulation_active": False,
        "read_only_scope": ["search", "listing_detail"],
        "write_actions": False,
        "login_stage": browser_status.get("login_stage", "idle"),
        "login_message": browser_status.get("login_message", ""),
        "worker_available": worker_available,
        "browser_ready": browser_status.get("browser_ready", False),
        "error_code": error_code,
        "error_message": error_message,
    }


@app.post("/api/v1/marketplace/auth/logout")
async def logout_marketplace_auth():
    if os.getenv("APP_MODE", "local").lower() == "live":
        return await _browser_worker_request("POST", "/auth/logout")
    try:
        for session_path in (SESSION_FILE, SESSION_METADATA_FILE):
            if os.path.exists(session_path):
                os.remove(session_path)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not remove browser session: {exc}")
    return {"status": "logged_out", "message": "Sessão do navegador removida."}
