"""Complete initial schema for the isolated validation database."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import packages.core.models

revision: str = "717b17623448"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The production-like PostgreSQL image includes pgvector. SQLite uses the
    # ORM's JSON fallback and does execute migrations in local validation.
    if op.get_context().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "products",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("brand", sa.String(), nullable=False),
        sa.Column("family", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("variant", sa.String(), nullable=True),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("canonical_tier", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_products_category", "products", ["category"])

    op.create_table(
        "listings",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("product_id", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("location_state", sa.String(), nullable=False),
        sa.Column("location_city", sa.String(), nullable=False),
        sa.Column("seller_name", sa.String(), nullable=False),
        sa.Column("seller_rating", sa.Float(), nullable=False),
        sa.Column("condition", sa.String(), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("source_url", sa.String(), nullable=False),
        sa.Column("first_seen", sa.DateTime(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("normalization_confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "external_id", name="uq_listings_source_external_id"),
    )
    op.create_index("ix_listings_external_id", "listings", ["external_id"])
    op.create_index("ix_listings_product_id", "listings", ["product_id"])
    op.create_index("ix_listings_status", "listings", ["status"])

    op.create_table(
        "product_knowledge",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("product_id", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("embedding", packages.core.models.VectorType(), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_knowledge_product_id", "product_knowledge", ["product_id"])
    op.create_index("ix_product_knowledge_category", "product_knowledge", ["category"])

    op.create_table(
        "listing_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("listing_id", sa.String(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_listing_snapshots_listing_id", "listing_snapshots", ["listing_id"])

    op.create_table(
        "market_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.String(), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("active_count", sa.Integer(), nullable=False),
        sa.Column("disappeared_count", sa.Integer(), nullable=False),
        sa.Column("asking_median", sa.Float(), nullable=False),
        sa.Column("estimated_clearing_value", sa.Float(), nullable=False),
        sa.Column("fast_sale_value", sa.Float(), nullable=False),
        sa.Column("robust_center", sa.Float(), nullable=False),
        sa.Column("p10", sa.Float(), nullable=False),
        sa.Column("p25", sa.Float(), nullable=False),
        sa.Column("median", sa.Float(), nullable=False),
        sa.Column("p75", sa.Float(), nullable=False),
        sa.Column("p90", sa.Float(), nullable=False),
        sa.Column("mad", sa.Float(), nullable=False),
        sa.Column("market_heat", sa.Float(), nullable=False),
        sa.Column("heat_band", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("listing_velocity", sa.Float(), nullable=False),
        sa.Column("disappearance_velocity", sa.Float(), nullable=False),
        sa.Column("median_visible_duration_days", sa.Float(), nullable=False),
        sa.Column("price_trend_30d", sa.Float(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_market_snapshots_product_id", "market_snapshots", ["product_id"])

    op.create_table(
        "opportunities",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("listing_id", sa.String(), nullable=False),
        sa.Column("product_id", sa.String(), nullable=False),
        sa.Column("price_edge", sa.Float(), nullable=False),
        sa.Column("final_score", sa.Float(), nullable=False),
        sa.Column("market_heat", sa.Float(), nullable=False),
        sa.Column("heat_band", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("score_breakdown", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("investigation", sa.JSON(), nullable=False),
        sa.Column("investigation_status", sa.String(), nullable=False),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_opportunities_listing_id", "opportunities", ["listing_id"])
    op.create_index("ix_opportunities_product_id", "opportunities", ["product_id"])

    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("processed_count", sa.Integer(), nullable=False),
        sa.Column("snapshots_created", sa.Integer(), nullable=False),
        sa.Column("products_normalized", sa.Integer(), nullable=False),
        sa.Column("opportunities_found", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pipeline_runs_status", "pipeline_runs", ["status"])

    op.create_table(
        "eval_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("dataset_version", sa.String(), nullable=False),
        sa.Column("configuration", sa.String(), nullable=False),
        sa.Column("executed_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("failures_count", sa.Integer(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "user_preferences",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("preferences", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("user_preferences")
    op.drop_table("eval_runs")
    op.drop_index("ix_pipeline_runs_status", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")
    op.drop_index("ix_opportunities_product_id", table_name="opportunities")
    op.drop_index("ix_opportunities_listing_id", table_name="opportunities")
    op.drop_table("opportunities")
    op.drop_index("ix_market_snapshots_product_id", table_name="market_snapshots")
    op.drop_table("market_snapshots")
    op.drop_index("ix_listing_snapshots_listing_id", table_name="listing_snapshots")
    op.drop_table("listing_snapshots")
    op.drop_index("ix_product_knowledge_category", table_name="product_knowledge")
    op.drop_index("ix_product_knowledge_product_id", table_name="product_knowledge")
    op.drop_table("product_knowledge")
    op.drop_index("ix_listings_status", table_name="listings")
    op.drop_index("ix_listings_product_id", table_name="listings")
    op.drop_index("ix_listings_external_id", table_name="listings")
    op.drop_table("listings")
    op.drop_index("ix_products_category", table_name="products")
    op.drop_table("products")
