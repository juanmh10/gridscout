"""Add live marketplace scopes and source-isolated run history."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9c1a8b2d4e6f"
down_revision: Union[str, Sequence[str], None] = "717b17623448"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("listings", sa.Column("marketplace_item_id", sa.String(), nullable=True))
    op.create_index("ix_listings_marketplace_item_id", "listings", ["marketplace_item_id"])

    op.add_column("listing_snapshots", sa.Column("pipeline_run_id", sa.String(), nullable=True))
    op.create_index("ix_listing_snapshots_pipeline_run_id", "listing_snapshots", ["pipeline_run_id"])
    if op.get_context().dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_listing_snapshots_pipeline_run_id",
            "listing_snapshots",
            "pipeline_runs",
            ["pipeline_run_id"],
            ["id"],
        )

    op.add_column("market_snapshots", sa.Column("source", sa.String(), nullable=True, server_default="legacy_mixed"))
    op.add_column("market_snapshots", sa.Column("pipeline_run_id", sa.String(), nullable=True))
    op.create_index("ix_market_snapshots_source", "market_snapshots", ["source"])
    op.create_index("ix_market_snapshots_pipeline_run_id", "market_snapshots", ["pipeline_run_id"])
    if op.get_context().dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_market_snapshots_pipeline_run_id",
            "market_snapshots",
            "pipeline_runs",
            ["pipeline_run_id"],
            ["id"],
        )

    op.add_column("opportunities", sa.Column("source", sa.String(), nullable=True, server_default="legacy_mixed"))
    op.add_column("opportunities", sa.Column("last_pipeline_run_id", sa.String(), nullable=True))
    op.add_column("opportunities", sa.Column("is_current", sa.Boolean(), nullable=True, server_default=sa.true()))
    op.create_index("ix_opportunities_source", "opportunities", ["source"])
    op.create_index("ix_opportunities_last_pipeline_run_id", "opportunities", ["last_pipeline_run_id"])
    op.create_index("ix_opportunities_is_current", "opportunities", ["is_current"])
    if op.get_context().dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_opportunities_last_pipeline_run_id",
            "opportunities",
            "pipeline_runs",
            ["last_pipeline_run_id"],
            ["id"],
        )

    op.create_table(
        "search_scopes",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("marketplace", sa.String(), nullable=False, server_default="olx"),
        sa.Column("product_id", sa.String(), nullable=True),
        sa.Column("query", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("min_price", sa.Float(), nullable=True),
        sa.Column("max_price", sa.Float(), nullable=True),
        sa.Column("sort", sa.String(), nullable=False, server_default="recent"),
        sa.Column("limit", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_search_scopes_marketplace", "search_scopes", ["marketplace"])
    op.create_index("ix_search_scopes_product_id", "search_scopes", ["product_id"])
    op.create_index("ix_search_scopes_enabled", "search_scopes", ["enabled"])

    op.create_table(
        "pipeline_run_scopes",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("pipeline_run_id", sa.String(), nullable=False),
        sa.Column("search_scope_id", sa.String(), nullable=True),
        sa.Column("scope_snapshot", sa.JSON(), nullable=False),
        sa.Column("search_url", sa.String(), nullable=False),
        sa.Column("total_results", sa.Integer(), nullable=False),
        sa.Column("cards_detected", sa.Integer(), nullable=False),
        sa.Column("items_returned", sa.Integer(), nullable=False),
        sa.Column("processed_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"]),
        sa.ForeignKeyConstraint(["search_scope_id"], ["search_scopes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pipeline_run_scopes_pipeline_run_id", "pipeline_run_scopes", ["pipeline_run_id"])
    op.create_index("ix_pipeline_run_scopes_search_scope_id", "pipeline_run_scopes", ["search_scope_id"])

    op.create_table(
        "pipeline_run_scope_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("pipeline_run_scope_id", sa.String(), nullable=False),
        sa.Column("listing_id", sa.String(), nullable=True),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["pipeline_run_scope_id"], ["pipeline_run_scopes.id"]),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pipeline_run_scope_id", "external_id", name="uq_run_scope_external_id"),
    )
    op.create_index("ix_pipeline_run_scope_items_pipeline_run_scope_id", "pipeline_run_scope_items", ["pipeline_run_scope_id"])
    op.create_index("ix_pipeline_run_scope_items_listing_id", "pipeline_run_scope_items", ["listing_id"])

    op.create_table(
        "opportunity_evaluations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("opportunity_id", sa.String(), nullable=False),
        sa.Column("pipeline_run_id", sa.String(), nullable=False),
        sa.Column("listing_id", sa.String(), nullable=False),
        sa.Column("product_id", sa.String(), nullable=False),
        sa.Column("price_edge", sa.Float(), nullable=False),
        sa.Column("final_score", sa.Float(), nullable=False),
        sa.Column("score_breakdown", sa.JSON(), nullable=False),
        sa.Column("investigation", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"]),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("opportunity_id", "pipeline_run_id", "listing_id", "product_id"):
        op.create_index(f"ix_opportunity_evaluations_{column}", "opportunity_evaluations", [column])


def downgrade() -> None:
    for column in ("product_id", "pipeline_run_id", "listing_id", "opportunity_id"):
        op.drop_index(f"ix_opportunity_evaluations_{column}", table_name="opportunity_evaluations")
    op.drop_table("opportunity_evaluations")
    op.drop_index("ix_pipeline_run_scope_items_listing_id", table_name="pipeline_run_scope_items")
    op.drop_index("ix_pipeline_run_scope_items_pipeline_run_scope_id", table_name="pipeline_run_scope_items")
    op.drop_table("pipeline_run_scope_items")
    op.drop_index("ix_pipeline_run_scopes_search_scope_id", table_name="pipeline_run_scopes")
    op.drop_index("ix_pipeline_run_scopes_pipeline_run_id", table_name="pipeline_run_scopes")
    op.drop_table("pipeline_run_scopes")
    op.drop_index("ix_search_scopes_enabled", table_name="search_scopes")
    op.drop_index("ix_search_scopes_product_id", table_name="search_scopes")
    op.drop_index("ix_search_scopes_marketplace", table_name="search_scopes")
    op.drop_table("search_scopes")
    if op.get_context().dialect.name != "sqlite":
        op.drop_constraint("fk_opportunities_last_pipeline_run_id", "opportunities", type_="foreignkey")
    op.drop_index("ix_opportunities_is_current", table_name="opportunities")
    op.drop_index("ix_opportunities_last_pipeline_run_id", table_name="opportunities")
    op.drop_index("ix_opportunities_source", table_name="opportunities")
    op.drop_column("opportunities", "is_current")
    op.drop_column("opportunities", "last_pipeline_run_id")
    op.drop_column("opportunities", "source")
    if op.get_context().dialect.name != "sqlite":
        op.drop_constraint("fk_market_snapshots_pipeline_run_id", "market_snapshots", type_="foreignkey")
    op.drop_index("ix_market_snapshots_pipeline_run_id", table_name="market_snapshots")
    op.drop_index("ix_market_snapshots_source", table_name="market_snapshots")
    op.drop_column("market_snapshots", "pipeline_run_id")
    op.drop_column("market_snapshots", "source")
    if op.get_context().dialect.name != "sqlite":
        op.drop_constraint("fk_listing_snapshots_pipeline_run_id", "listing_snapshots", type_="foreignkey")
    op.drop_index("ix_listing_snapshots_pipeline_run_id", table_name="listing_snapshots")
    op.drop_column("listing_snapshots", "pipeline_run_id")
    op.drop_index("ix_listings_marketplace_item_id", table_name="listings")
    op.drop_column("listings", "marketplace_item_id")
