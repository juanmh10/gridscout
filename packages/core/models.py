import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import String, Float, Integer, Boolean, DateTime, JSON, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator
from packages.core.database import Base

class VectorType(TypeDecorator):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            try:
                from pgvector.sqlalchemy import Vector
                return dialect.type_descriptor(Vector(768))
            except ImportError:
                pass
        return dialect.type_descriptor(JSON())

def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class MarketCohort(Base):
    __tablename__ = "market_cohorts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    category: Mapped[str] = mapped_column(String, nullable=False, index=True)
    cohort_key: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    taxonomy_version: Mapped[str] = mapped_column(String, default="hardware-taxonomy-v2")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)

    products = relationship("Product", back_populates="market_cohort")
    market_snapshots = relationship("MarketSnapshot", back_populates="market_cohort")
    listings = relationship("Listing", back_populates="market_cohort")


class Product(Base):
    __tablename__ = "products"
    
    id: Mapped[str] = mapped_column(String, primary_key=True)
    category: Mapped[str] = mapped_column(String, nullable=False, index=True)
    brand: Mapped[str] = mapped_column(String, nullable=False)
    family: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    variant: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    canonical_tier: Mapped[int] = mapped_column(Integer, default=1)
    market_cohort_id: Mapped[Optional[str]] = mapped_column(ForeignKey("market_cohorts.id"), nullable=True, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)

    market_cohort = relationship("MarketCohort", back_populates="products")
    listings = relationship("Listing", back_populates="product")
    market_snapshots = relationship("MarketSnapshot", back_populates="product")


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_listings_source_external_id"),
    )
    
    id: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[str] = mapped_column(String, default="fixture")
    external_id: Mapped[str] = mapped_column(String, index=True)
    marketplace_item_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    product_id: Mapped[Optional[str]] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    market_cohort_id: Mapped[Optional[str]] = mapped_column(ForeignKey("market_cohorts.id"), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String, default="other", index=True)
    item_form: Mapped[str] = mapped_column(String, default="standalone", index=True)
    classification_status: Mapped[str] = mapped_column(String, default="unverified", index=True)
    classification_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    taxonomy_version: Mapped[str] = mapped_column(String, default="hardware-taxonomy-v2")
    schema_version: Mapped[str] = mapped_column(String, default="hardware-schema-v2")
    analytics_eligible: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    exclusion_codes: Mapped[list] = mapped_column(JSON, default=list)
    classification_evidence: Mapped[list] = mapped_column(JSON, default=list)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    # A card returned by marketplace search is useful evidence even when it
    # does not expose these page-only fields.  Unknown must stay unknown; old
    # defaults made a card look like it had been audited.
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    location_state: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    location_city: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    seller_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    seller_rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    condition: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    delivery_status: Mapped[str] = mapped_column(String, default="UNKNOWN", index=True)
    delivery_evidence: Mapped[list] = mapped_column(JSON, default=list)
    seller_verification: Mapped[str] = mapped_column(String, default="UNKNOWN", index=True)
    seller_signal_level: Mapped[str] = mapped_column(String, default="UNKNOWN", index=True)
    seller_evidence: Mapped[list] = mapped_column(JSON, default=list)
    analysis_summary: Mapped[str] = mapped_column(Text, default="")
    analysis_status: Mapped[str] = mapped_column(String, default="unavailable", index=True)
    analysis_updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    source_url: Mapped[str] = mapped_column(String, default="")
    first_seen: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)
    last_seen: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)
    status: Mapped[str] = mapped_column(String, default="active", index=True)
    normalization_confidence: Mapped[float] = mapped_column(Float, default=0.95)
    evidence_level: Mapped[str] = mapped_column(String, default="detail", index=True)
    audit_status: Mapped[str] = mapped_column(String, default="audited", index=True)
    evidence_provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    last_audited_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)

    market_cohort = relationship("MarketCohort", back_populates="listings")
    product = relationship("Product", back_populates="listings")
    snapshots = relationship("ListingSnapshot", back_populates="listing", cascade="all, delete-orphan")
    opportunities = relationship("Opportunity", back_populates="listing", cascade="all, delete-orphan")
    analyses = relationship("ListingAnalysis", back_populates="listing", cascade="all, delete-orphan")


class ListingSnapshot(Base):
    __tablename__ = "listing_snapshots"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[str] = mapped_column(ForeignKey("listings.id"), nullable=False, index=True)
    pipeline_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=True, index=True)
    product_id: Mapped[Optional[str]] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    market_cohort_id: Mapped[Optional[str]] = mapped_column(ForeignKey("market_cohorts.id"), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String, default="other", index=True)
    item_form: Mapped[str] = mapped_column(String, default="standalone")
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    classification_status: Mapped[str] = mapped_column(String, default="unverified")
    classification_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    taxonomy_version: Mapped[str] = mapped_column(String, default="hardware-taxonomy-v2")
    schema_version: Mapped[str] = mapped_column(String, default="hardware-schema-v2")
    analytics_eligible: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    exclusion_codes: Mapped[list] = mapped_column(JSON, default=list)
    classification_evidence: Mapped[list] = mapped_column(JSON, default=list)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, default="active")
    evidence_level: Mapped[str] = mapped_column(String, default="detail", index=True)
    listing_discovery_id: Mapped[Optional[str]] = mapped_column(ForeignKey("listing_discoveries.id"), nullable=True, index=True)
    observed_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)

    listing = relationship("Listing", back_populates="snapshots")
    pipeline_run = relationship("PipelineRun", back_populates="listing_snapshots")
    product = relationship("Product")
    market_cohort = relationship("MarketCohort")


class ProductKnowledge(Base):
    __tablename__ = "product_knowledge"
    
    id: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[Optional[str]] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    embedding = mapped_column(VectorType())


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[Optional[str]] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    market_cohort_id: Mapped[Optional[str]] = mapped_column(ForeignKey("market_cohorts.id"), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String, default="gpu", index=True)
    source: Mapped[str] = mapped_column(String, default="legacy_mixed", index=True)
    evidence_tier: Mapped[str] = mapped_column(String, default="audited", index=True)
    pipeline_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=True, index=True)
    taxonomy_version: Mapped[str] = mapped_column(String, default="hardware-taxonomy-v2")
    window_days: Mapped[int] = mapped_column(Integer, default=30)
    window_start: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    window_end: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    active_count: Mapped[int] = mapped_column(Integer, default=0)
    disappeared_count: Mapped[int] = mapped_column(Integer, default=0)
    included_count: Mapped[int] = mapped_column(Integer, default=0)
    unverified_count: Mapped[int] = mapped_column(Integer, default=0)
    excluded_count: Mapped[int] = mapped_column(Integer, default=0)
    exclusion_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    cohort_fingerprint: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_legacy: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    asking_median: Mapped[float] = mapped_column(Float, default=0.0)
    estimated_clearing_value: Mapped[float] = mapped_column(Float, default=0.0)
    fast_sale_value: Mapped[float] = mapped_column(Float, default=0.0)
    robust_center: Mapped[float] = mapped_column(Float, default=0.0)
    p10: Mapped[float] = mapped_column(Float, default=0.0)
    p25: Mapped[float] = mapped_column(Float, default=0.0)
    median: Mapped[float] = mapped_column(Float, default=0.0)
    p75: Mapped[float] = mapped_column(Float, default=0.0)
    p90: Mapped[float] = mapped_column(Float, default=0.0)
    mad: Mapped[float] = mapped_column(Float, default=0.0)
    market_heat: Mapped[float] = mapped_column(Float, default=0.0)
    heat_band: Mapped[str] = mapped_column(String, default="NORMAL")
    confidence: Mapped[float] = mapped_column(Float, default=0.90)
    listing_velocity: Mapped[float] = mapped_column(Float, default=1.0)
    disappearance_velocity: Mapped[float] = mapped_column(Float, default=0.8)
    median_visible_duration_days: Mapped[float] = mapped_column(Float, default=5.0)
    price_trend_30d: Mapped[float] = mapped_column(Float, default=-0.02)
    calculated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)

    product = relationship("Product", back_populates="market_snapshots")
    market_cohort = relationship("MarketCohort", back_populates="market_snapshots")
    members = relationship("MarketSnapshotMember", back_populates="market_snapshot", cascade="all, delete-orphan")


class MarketSnapshotMember(Base):
    __tablename__ = "market_snapshot_members"
    __table_args__ = (
        UniqueConstraint("market_snapshot_id", "listing_snapshot_id", name="uq_market_snapshot_member"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_snapshot_id: Mapped[int] = mapped_column(ForeignKey("market_snapshots.id"), nullable=False, index=True)
    listing_snapshot_id: Mapped[int] = mapped_column(ForeignKey("listing_snapshots.id"), nullable=False, index=True)
    included: Mapped[bool] = mapped_column(Boolean, default=True)
    role: Mapped[str] = mapped_column(String, default="active")
    exclusion_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)

    market_snapshot = relationship("MarketSnapshot", back_populates="members")
    listing_snapshot = relationship("ListingSnapshot")


class Opportunity(Base):
    __tablename__ = "opportunities"
    
    id: Mapped[str] = mapped_column(String, primary_key=True)
    listing_id: Mapped[str] = mapped_column(ForeignKey("listings.id"), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    listing_snapshot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("listing_snapshots.id"), nullable=True, index=True)
    market_snapshot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("market_snapshots.id"), nullable=True, index=True)
    market_cohort_id: Mapped[Optional[str]] = mapped_column(ForeignKey("market_cohorts.id"), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String, default="gpu", index=True)
    source: Mapped[str] = mapped_column(String, default="legacy_mixed", index=True)
    last_pipeline_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=True, index=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    price_edge: Mapped[float] = mapped_column(Float, default=0.0)
    final_score: Mapped[float] = mapped_column(Float, default=0.0)
    market_heat: Mapped[float] = mapped_column(Float, default=50.0)
    heat_band: Mapped[str] = mapped_column(String, default="NORMAL")
    confidence: Mapped[float] = mapped_column(Float, default=0.90)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    explanation: Mapped[str] = mapped_column(Text, default="")
    investigation: Mapped[dict] = mapped_column(JSON, default=dict)
    investigation_status: Mapped[str] = mapped_column(String, default="completed")
    computed_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)

    listing = relationship("Listing", back_populates="opportunities")
    listing_snapshot = relationship("ListingSnapshot")
    market_snapshot = relationship("MarketSnapshot")
    market_cohort = relationship("MarketCohort")


class OpportunitySignal(Base):
    """Card-only opportunity signal that never masquerades as a full listing."""

    __tablename__ = "opportunity_signals"
    __table_args__ = (UniqueConstraint("profile_id", "listing_discovery_id", name="uq_signal_profile_discovery"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id"), nullable=False, index=True)
    pipeline_run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False, index=True)
    listing_discovery_id: Mapped[str] = mapped_column(ForeignKey("listing_discoveries.id"), nullable=False, index=True)
    opportunity_id: Mapped[Optional[str]] = mapped_column(ForeignKey("opportunities.id"), nullable=True, index=True)
    stage: Mapped[str] = mapped_column(String, default="preliminary", index=True)
    preliminary_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    scorer_version: Mapped[str] = mapped_column(String, default="card-signal-v1")
    eligibility_reasons: Mapped[list] = mapped_column(JSON, default=list)
    full_flow_completed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)

    discovery = relationship("ListingDiscovery", back_populates="opportunity_signal")
    opportunity = relationship("Opportunity")


class Profile(Base):
    """A named local workspace.

    Market records are deliberately not attached to a profile.  This model is
    only the owner of user-authored configuration, searches, run history and
    feedback.
    """

    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # Kept separately so the uniqueness rule is portable between SQLite and
    # PostgreSQL (and includes whitespace/accent/case normalisation in the API).
    name_normalized: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)

    search_definitions = relationship("SearchDefinition", back_populates="profile", cascade="all, delete-orphan")
    search_sessions = relationship("SearchSession", back_populates="profile", cascade="all, delete-orphan")
    search_scopes = relationship("SearchScope", back_populates="profile")
    pipeline_runs = relationship("PipelineRun", back_populates="profile")
    feedback = relationship("OpportunityFeedback", back_populates="profile", cascade="all, delete-orphan")
    preference_records = relationship("UserPreference", back_populates="profile")


class SearchDefinition(Base):
    """A profile-owned, versioned intent and plan for a reusable search."""

    __tablename__ = "search_definitions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    intent: Mapped[dict] = mapped_column(JSON, default=dict)
    intent_version: Mapped[int] = mapped_column(Integer, default=1)
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    plan_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)

    profile = relationship("Profile", back_populates="search_definitions")
    sessions = relationship("SearchSession", back_populates="search_definition")


class SearchSession(Base):
    """Persisted draft/confirmation state for one profile's search flow."""

    __tablename__ = "search_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id"), nullable=False, index=True)
    search_definition_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("search_definitions.id"), nullable=True, index=True
    )
    pipeline_run_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("pipeline_runs.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String, default="draft", index=True)
    draft: Mapped[dict] = mapped_column(JSON, default=dict)
    # Stable public shape when a plan needs input: {code, message, fields}.
    clarification: Mapped[dict] = mapped_column(JSON, default=dict)
    confirmed_plan: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    execution: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)

    profile = relationship("Profile", back_populates="search_sessions")
    search_definition = relationship("SearchDefinition", back_populates="sessions")
    pipeline_run = relationship("PipelineRun", back_populates="search_sessions")
    messages = relationship(
        "SearchSessionMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="SearchSessionMessage.created_at",
    )


class SearchSessionMessage(Base):
    __tablename__ = "search_session_messages"
    __table_args__ = (
        UniqueConstraint(
            "search_session_id",
            "client_request_id",
            name="uq_search_session_messages_session_request",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    search_session_id: Mapped[str] = mapped_column(ForeignKey("search_sessions.id"), nullable=False, index=True)
    client_request_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow, index=True)

    session = relationship("SearchSession", back_populates="messages")


class OpportunityFeedback(Base):
    """A profile's independent annotation for a global listing/opportunity."""

    __tablename__ = "opportunity_feedback"
    __table_args__ = (
        UniqueConstraint("profile_id", "listing_id", name="uq_opportunity_feedback_profile_listing"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id"), nullable=False, index=True)
    listing_id: Mapped[str] = mapped_column(ForeignKey("listings.id"), nullable=False, index=True)
    feedback: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)

    profile = relationship("Profile", back_populates="feedback")
    listing = relationship("Listing")


class ListingAnalysis(Base):
    """Immutable structured model output for one listing/pipeline stage."""

    __tablename__ = "listing_analyses"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    listing_id: Mapped[str] = mapped_column(ForeignKey("listings.id"), nullable=False, index=True)
    pipeline_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=True, index=True)
    stage: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    photos_examined: Mapped[int] = mapped_column(Integer, default=0)
    external_navigation_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)

    listing = relationship("Listing", back_populates="analyses")
    pipeline_run = relationship("PipelineRun")


class SearchScope(Base):
    __tablename__ = "search_scopes"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    marketplace: Mapped[str] = mapped_column(String, default="olx", index=True)
    product_id: Mapped[Optional[str]] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    query: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    min_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sort: Mapped[str] = mapped_column(String, default="recent")
    limit: Mapped[int] = mapped_column(Integer, default=10)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)

    product = relationship("Product")
    profile = relationship("Profile", back_populates="search_scopes")
    pipeline_scopes = relationship("PipelineRunScope", back_populates="search_scope")


class PipelineRunScope(Base):
    __tablename__ = "pipeline_run_scopes"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    pipeline_run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False, index=True)
    search_scope_id: Mapped[Optional[str]] = mapped_column(ForeignKey("search_scopes.id"), nullable=True, index=True)
    scope_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    search_url: Mapped[str] = mapped_column(String, default="")
    total_results: Mapped[int] = mapped_column(Integer, default=0)
    cards_detected: Mapped[int] = mapped_column(Integer, default=0)
    items_returned: Mapped[int] = mapped_column(Integer, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="pending")
    error_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)

    pipeline_run = relationship("PipelineRun", back_populates="scope_runs")
    search_scope = relationship("SearchScope", back_populates="pipeline_scopes")
    items = relationship("PipelineRunScopeItem", back_populates="run_scope", cascade="all, delete-orphan")


class PipelineRunScopeItem(Base):
    __tablename__ = "pipeline_run_scope_items"
    __table_args__ = (UniqueConstraint("pipeline_run_scope_id", "external_id", name="uq_run_scope_external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_run_scope_id: Mapped[str] = mapped_column(ForeignKey("pipeline_run_scopes.id"), nullable=False, index=True)
    listing_id: Mapped[Optional[str]] = mapped_column(ForeignKey("listings.id"), nullable=True, index=True)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="discovered")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    match_status: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    match_details: Mapped[dict] = mapped_column(JSON, default=dict)

    run_scope = relationship("PipelineRunScope", back_populates="items")
    listing = relationship("Listing")


class OpportunityEvaluation(Base):
    __tablename__ = "opportunity_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[str] = mapped_column(ForeignKey("opportunities.id"), nullable=False, index=True)
    pipeline_run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False, index=True)
    listing_id: Mapped[str] = mapped_column(ForeignKey("listings.id"), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    listing_snapshot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("listing_snapshots.id"), nullable=True, index=True)
    market_snapshot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("market_snapshots.id"), nullable=True, index=True)
    market_cohort_id: Mapped[Optional[str]] = mapped_column(ForeignKey("market_cohorts.id"), nullable=True, index=True)
    category: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    price_edge: Mapped[float] = mapped_column(Float, default=0.0)
    final_score: Mapped[float] = mapped_column(Float, default=0.0)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    investigation: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, default="scored")
    evaluated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)

    listing_snapshot = relationship("ListingSnapshot")
    market_snapshot = relationship("MarketSnapshot")
    market_cohort = relationship("MarketCohort")

class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    
    id: Mapped[str] = mapped_column(String, primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String, default="synthetic_full_ingest")
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    snapshots_created: Mapped[int] = mapped_column(Integer, default=0)
    products_normalized: Mapped[int] = mapped_column(Integer, default=0)
    opportunities_found: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    # High-volume runs retain their scheduling state in the database so a
    # worker restart never turns an OLX cooldown into lost discovery work.
    workload_mode: Mapped[str] = mapped_column(String, default="standard", index=True)
    workload_state: Mapped[dict] = mapped_column(JSON, default=dict)
    workload_not_before: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True, index=True)
    workload_deadline: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)

    profile = relationship("Profile", back_populates="pipeline_runs")
    scope_runs = relationship("PipelineRunScope", back_populates="pipeline_run", cascade="all, delete-orphan")
    listing_snapshots = relationship("ListingSnapshot", back_populates="pipeline_run")
    chat_thread = relationship("PipelineChatThread", back_populates="pipeline_run", uselist=False, cascade="all, delete-orphan")
    search_sessions = relationship("SearchSession", back_populates="pipeline_run")
    retrieval_tasks = relationship("PipelineRetrievalTask", back_populates="pipeline_run", cascade="all, delete-orphan")
    discoveries = relationship("ListingDiscovery", back_populates="pipeline_run", cascade="all, delete-orphan")
    enrichment_tasks = relationship("ListingEnrichmentTask", back_populates="pipeline_run", cascade="all, delete-orphan")
    navigation_ledger = relationship("PipelineNavigationLedger", back_populates="pipeline_run", cascade="all, delete-orphan")
    dataset_analysis = relationship("PipelineDatasetAnalysis", back_populates="pipeline_run", uselist=False, cascade="all, delete-orphan")
    attempts = relationship("PipelineWorkloadAttempt", back_populates="pipeline_run", cascade="all, delete-orphan", order_by="PipelineWorkloadAttempt.attempt_number")
    state_transitions = relationship("PipelineStateTransition", back_populates="pipeline_run", cascade="all, delete-orphan", order_by="PipelineStateTransition.occurred_at")
    parser_artifacts = relationship("PipelineParserArtifact", back_populates="pipeline_run", cascade="all, delete-orphan")


class PipelineRetrievalTask(Base):
    """One persisted page navigation in a high-volume discovery run."""

    __tablename__ = "pipeline_retrieval_tasks"
    __table_args__ = (UniqueConstraint("pipeline_run_id", "pipeline_run_scope_id", "page", name="uq_retrieval_task_run_scope_page"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    pipeline_run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False, index=True)
    pipeline_run_scope_id: Mapped[str] = mapped_column(ForeignKey("pipeline_run_scopes.id"), nullable=False, index=True)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    not_before: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_owner: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    lease_token: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    lease_expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True, index=True)
    heartbeat_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    observed_count: Mapped[int] = mapped_column(Integer, default=0)
    discovered_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_error_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_error_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)
    started_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)

    pipeline_run = relationship("PipelineRun", back_populates="retrieval_tasks")
    scope_run = relationship("PipelineRunScope")
    discoveries = relationship("ListingDiscovery", back_populates="retrieval_task")


class ListingDiscovery(Base):
    """Deduplicated summary evidence, kept before any listing-detail visit."""

    __tablename__ = "listing_discoveries"
    __table_args__ = (UniqueConstraint("pipeline_run_id", "canonical_key", name="uq_listing_discovery_run_canonical"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    pipeline_run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False, index=True)
    pipeline_retrieval_task_id: Mapped[Optional[str]] = mapped_column(ForeignKey("pipeline_retrieval_tasks.id"), nullable=True, index=True)
    pipeline_run_scope_id: Mapped[Optional[str]] = mapped_column(ForeignKey("pipeline_run_scopes.id"), nullable=True, index=True)
    canonical_key: Mapped[str] = mapped_column(String, nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    marketplace_item_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    normalized_url: Mapped[str] = mapped_column(String, default="")
    title: Mapped[str] = mapped_column(String, default="")
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=None)
    price_origin: Mapped[str] = mapped_column(String, default="dom")
    price_raw: Mapped[str] = mapped_column(String, default="")
    location: Mapped[str] = mapped_column(String, default="")
    location_origin: Mapped[str] = mapped_column(String, default="dom")
    condition: Mapped[str] = mapped_column(String, default="")
    seller: Mapped[str] = mapped_column(String, default="")
    raw_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    field_coverage: Mapped[dict] = mapped_column(JSON, default=dict)
    parser_version: Mapped[str] = mapped_column(String, default="olx-dom-2026-08-24")
    triage_status: Mapped[str] = mapped_column(String, default="pending", index=True)
    triage_reason: Mapped[str] = mapped_column(String, default="")
    priority_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    # Card-level model output is intentionally separate from a fully opened
    # Listing.  It can identify title evidence, but never confirms page-only
    # fields such as seller, delivery, condition or availability.
    card_analysis_status: Mapped[str] = mapped_column(String, default="not_requested", index=True)
    card_analysis_result: Mapped[dict] = mapped_column(JSON, default=dict)
    card_analysis_model_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    card_analysis_contract_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    card_analysis_evidence_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    card_analysis_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    card_analysis_attempts: Mapped[int] = mapped_column(Integer, default=0)
    card_analysis_updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    # Rule output is intentionally independent from the optional semantic
    # agent.  A deterministic classification can publish a card immediately
    # while an ambiguous card waits for the agent in the background.
    rule_analysis_status: Mapped[str] = mapped_column(String, default="pending", index=True)
    rule_analysis_result: Mapped[dict] = mapped_column(JSON, default=dict)
    rule_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Durable decision of the price/scope/prefilter gate.  Raw discovery data
    # remains untouched when a card is rejected.
    gate_status: Mapped[str] = mapped_column(String, default="observed", index=True)
    gate_reason_code: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    gate_evidence: Mapped[list] = mapped_column(JSON, default=list)
    gate_evidence_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    publication_status: Mapped[str] = mapped_column(String, default="observed", index=True)
    publication_reason: Mapped[str] = mapped_column(String, default="")
    listing_id: Mapped[Optional[str]] = mapped_column(ForeignKey("listings.id"), nullable=True, index=True)
    first_observed_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)
    last_observed_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)

    pipeline_run = relationship("PipelineRun", back_populates="discoveries")
    retrieval_task = relationship("PipelineRetrievalTask", back_populates="discoveries")
    scope_run = relationship("PipelineRunScope")
    enrichment_task = relationship("ListingEnrichmentTask", back_populates="discovery", uselist=False, cascade="all, delete-orphan")
    opportunity_signal = relationship("OpportunitySignal", back_populates="discovery", uselist=False, cascade="all, delete-orphan")
    listing = relationship("Listing", foreign_keys=[listing_id])


class ListingEnrichmentTask(Base):
    """Durable detail capture task for a selected discovery candidate."""

    __tablename__ = "listing_enrichment_tasks"
    __table_args__ = (UniqueConstraint("pipeline_run_id", "listing_discovery_id", name="uq_enrichment_task_run_discovery"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    pipeline_run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False, index=True)
    listing_discovery_id: Mapped[str] = mapped_column(ForeignKey("listing_discoveries.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    not_before: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_owner: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    lease_token: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    lease_expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True, index=True)
    heartbeat_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    listing_id: Mapped[Optional[str]] = mapped_column(ForeignKey("listings.id"), nullable=True, index=True)
    error_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_error_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_error_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)
    started_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)

    pipeline_run = relationship("PipelineRun", back_populates="enrichment_tasks")
    discovery = relationship("ListingDiscovery", back_populates="enrichment_task")
    listing = relationship("Listing")



class PipelineNavigationLedger(Base):
    """Audit ledger for guarded external visits; IDs remain diagnostic-only."""

    __tablename__ = "pipeline_navigation_ledger"
    __table_args__ = (UniqueConstraint("pipeline_run_id", "task_kind", "task_id", name="uq_navigation_ledger_task"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    pipeline_run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False, index=True)
    task_kind: Mapped[str] = mapped_column(String, nullable=False)
    task_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="completed", index=True)
    scheduled_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)

    pipeline_run = relationship("PipelineRun", back_populates="navigation_ledger")


class PipelineDatasetAnalysis(Base):
    """Immutable-source analysis of a high-volume discovery dataset.

    The raw search-card evidence stays on ``ListingDiscovery``.  This record
    holds only deterministic derivations, capture-quality measurements and an
    optional model-written operational summary, so summary-only results never
    masquerade as fully opened listing pages.
    """

    __tablename__ = "pipeline_dataset_analyses"
    __table_args__ = (UniqueConstraint("pipeline_run_id", name="uq_pipeline_dataset_analysis_run"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    pipeline_run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    schema_version: Mapped[str] = mapped_column(String, default="high-volume-dataset-analysis-v1")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    agent_status: Mapped[str] = mapped_column(String, default="not_requested", index=True)
    agent_model_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    agent_summary: Mapped[str] = mapped_column(Text, default="")
    agent_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)

    pipeline_run = relationship("PipelineRun", back_populates="dataset_analysis")


class PipelineChatThread(Base):
    """One persisted analysis conversation for a completed pipeline run."""

    __tablename__ = "pipeline_chat_threads"
    __table_args__ = (UniqueConstraint("pipeline_run_id", name="uq_pipeline_chat_threads_pipeline_run_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    pipeline_run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False, index=True)
    next_offset: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)

    pipeline_run = relationship("PipelineRun", back_populates="chat_thread")
    messages = relationship(
        "PipelineChatMessage",
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="PipelineChatMessage.created_at",
    )


class PipelineChatMessage(Base):
    """Structured, UI-safe history. Model telemetry intentionally never stores this content."""

    __tablename__ = "pipeline_chat_messages"
    __table_args__ = (
        UniqueConstraint("thread_id", "client_request_id", name="uq_pipeline_chat_messages_thread_request"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("pipeline_chat_threads.id"), nullable=False, index=True)
    client_request_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    actions: Mapped[list] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow, index=True)

    thread = relationship("PipelineChatThread", back_populates="messages")

class EvalRun(Base):
    __tablename__ = "eval_runs"
    
    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_version: Mapped[str] = mapped_column(String, default="v1.0.0")
    configuration: Mapped[str] = mapped_column(String, default="deterministic_local_v1")
    executed_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)
    status: Mapped[str] = mapped_column(String, default="completed")
    failures_count: Mapped[int] = mapped_column(Integer, default=0)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    details: Mapped[dict] = mapped_column(JSON, default=dict)

class UserPreference(Base):
    __tablename__ = "user_preferences"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id"), nullable=False, index=True)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)

    profile = relationship("Profile", back_populates="preference_records")


class PipelineWorkloadAttempt(Base):
    """Historical execution attempt for a pipeline run."""

    __tablename__ = "pipeline_workload_attempts"
    __table_args__ = (
        UniqueConstraint("pipeline_run_id", "attempt_number", name="uq_pipeline_workload_attempt_run_number"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    pipeline_run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String, default="initial", index=True)  # initial | automatic_recovery | operator_resume
    status: Mapped[str] = mapped_column(String, default="running", index=True)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)
    deadline_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    worker_revision: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    parser_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)

    pipeline_run = relationship("PipelineRun", back_populates="attempts")


class PipelineStateTransition(Base):
    """Immutable transition history between pipeline execution states."""

    __tablename__ = "pipeline_state_transitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=False, index=True)
    from_status: Mapped[str] = mapped_column(String, nullable=False)
    to_status: Mapped[str] = mapped_column(String, nullable=False)
    reason_code: Mapped[str] = mapped_column(String, nullable=False)
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    actor: Mapped[str] = mapped_column(String, default="scheduler")  # scheduler | worker | operator
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    pipeline_run = relationship("PipelineRun", back_populates="state_transitions")


class PipelineParserArtifact(Base):
    """Diagnostic artifact for parser failures and contract mismatches."""

    __tablename__ = "pipeline_parser_artifacts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    pipeline_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("pipeline_runs.id"), nullable=True, index=True)
    task_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    attempt_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    parser_version: Mapped[str] = mapped_column(String, nullable=False)
    build_sha: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    stage: Mapped[str] = mapped_column(String, default="detail", index=True)  # search | detail
    url_hash: Mapped[str] = mapped_column(String, nullable=False, index=True)
    page_state: Mapped[str] = mapped_column(String, default="unknown")
    http_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    missing_fields: Mapped[list] = mapped_column(JSON, default=list)
    selectors_tried: Mapped[int] = mapped_column(Integer, default=0)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    sanitized_snippet: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)

    pipeline_run = relationship("PipelineRun", back_populates="parser_artifacts")


class ModelMetricCall(Base):
    """Metadata-only record of a model invocation; prompts and responses are never stored."""

    __tablename__ = "model_metric_calls"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    pipeline_run_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("pipeline_runs.id"), nullable=True, index=True
    )
    attempt_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    task_kind: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    task_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    origin: Mapped[str] = mapped_column(String, default="pipeline", index=True)
    operation: Mapped[str] = mapped_column(String, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    auth_mode: Mapped[str] = mapped_column(String, nullable=False)
    model_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    duration_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    candidates_tokens: Mapped[int] = mapped_column(Integer, default=0)
    thoughts_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    tool_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    finish_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    estimated_cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pricing_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    normalization_quality: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    response_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    provider_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
