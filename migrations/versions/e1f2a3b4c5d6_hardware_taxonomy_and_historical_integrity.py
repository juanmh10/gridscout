"""Hardware taxonomy, market cohorts, snapshot members, and temporal integrity.

Revision ID: e1f2a3b4c5d6
Revises: c6f8a2d9e4b7
"""

from alembic import op
import sqlalchemy as sa


revision = "e1f2a3b4c5d6"
down_revision = "c6f8a2d9e4b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())

    # 1. Create market_cohorts if not exists
    if "market_cohorts" not in existing_tables:
        op.create_table(
            "market_cohorts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("category", sa.String(), nullable=False),
            sa.Column("cohort_key", sa.String(), nullable=False, unique=True),
            sa.Column("display_name", sa.String(), nullable=False),
            sa.Column("attributes", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("taxonomy_version", sa.String(), nullable=False, server_default="hardware-taxonomy-v2"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_market_cohorts_category", "market_cohorts", ["category"])
        op.create_index("ix_market_cohorts_cohort_key", "market_cohorts", ["cohort_key"])

    # 2. Add market_cohort_id to products
    product_cols = {col["name"] for col in inspector.get_columns("products")}
    if "market_cohort_id" not in product_cols:
        op.add_column("products", sa.Column("market_cohort_id", sa.String(), nullable=True))
        op.create_index("ix_products_market_cohort_id", "products", ["market_cohort_id"])

    # 3. Add category, item_form, classification fields to listings
    listing_cols = {col["name"] for col in inspector.get_columns("listings")}
    listing_additions = [
        ("market_cohort_id", sa.Column("market_cohort_id", sa.String(), nullable=True)),
        ("category", sa.Column("category", sa.String(), nullable=False, server_default="other")),
        ("item_form", sa.Column("item_form", sa.String(), nullable=False, server_default="standalone")),
        ("classification_status", sa.Column("classification_status", sa.String(), nullable=False, server_default="unverified")),
        ("classification_confidence", sa.Column("classification_confidence", sa.Float(), nullable=False, server_default="0.0")),
        ("taxonomy_version", sa.Column("taxonomy_version", sa.String(), nullable=False, server_default="hardware-taxonomy-v2")),
        ("schema_version", sa.Column("schema_version", sa.String(), nullable=False, server_default="hardware-schema-v2")),
        ("analytics_eligible", sa.Column("analytics_eligible", sa.Boolean(), nullable=False, server_default=sa.text("0" if op.get_bind().dialect.name == "sqlite" else "false"))),
        ("exclusion_codes", sa.Column("exclusion_codes", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))),
        ("classification_evidence", sa.Column("classification_evidence", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))),
    ]
    for col_name, col_def in listing_additions:
        if col_name not in listing_cols:
            op.add_column("listings", col_def)
    if "category" not in listing_cols:
        op.create_index("ix_listings_category", "listings", ["category"])
    if "market_cohort_id" not in listing_cols:
        op.create_index("ix_listings_market_cohort_id", "listings", ["market_cohort_id"])

    # 4. Add temporal fact fields to listing_snapshots
    snapshot_cols = {col["name"] for col in inspector.get_columns("listing_snapshots")}
    snapshot_additions = [
        ("product_id", sa.Column("product_id", sa.String(), nullable=True)),
        ("market_cohort_id", sa.Column("market_cohort_id", sa.String(), nullable=True)),
        ("category", sa.Column("category", sa.String(), nullable=False, server_default="other")),
        ("item_form", sa.Column("item_form", sa.String(), nullable=False, server_default="standalone")),
        ("attributes", sa.Column("attributes", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))),
        ("classification_status", sa.Column("classification_status", sa.String(), nullable=False, server_default="unverified")),
        ("classification_confidence", sa.Column("classification_confidence", sa.Float(), nullable=False, server_default="0.0")),
        ("taxonomy_version", sa.Column("taxonomy_version", sa.String(), nullable=False, server_default="hardware-taxonomy-v2")),
        ("schema_version", sa.Column("schema_version", sa.String(), nullable=False, server_default="hardware-schema-v2")),
        ("analytics_eligible", sa.Column("analytics_eligible", sa.Boolean(), nullable=False, server_default=sa.text("0" if op.get_bind().dialect.name == "sqlite" else "false"))),
        ("exclusion_codes", sa.Column("exclusion_codes", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))),
        ("classification_evidence", sa.Column("classification_evidence", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))),
    ]
    for col_name, col_def in snapshot_additions:
        if col_name not in snapshot_cols:
            op.add_column("listing_snapshots", col_def)
    if "product_id" not in snapshot_cols:
        op.create_index("ix_listing_snapshots_product_id", "listing_snapshots", ["product_id"])
    if "market_cohort_id" not in snapshot_cols:
        op.create_index("ix_listing_snapshots_market_cohort_id", "listing_snapshots", ["market_cohort_id"])

    # 5. Add lineage and quality fields to market_snapshots
    market_cols = {col["name"] for col in inspector.get_columns("market_snapshots")}
    market_additions = [
        ("market_cohort_id", sa.Column("market_cohort_id", sa.String(), nullable=True)),
        ("category", sa.Column("category", sa.String(), nullable=False, server_default="gpu")),
        ("taxonomy_version", sa.Column("taxonomy_version", sa.String(), nullable=False, server_default="hardware-taxonomy-v2")),
        ("window_start", sa.Column("window_start", sa.DateTime(), nullable=True)),
        ("window_end", sa.Column("window_end", sa.DateTime(), nullable=True)),
        ("included_count", sa.Column("included_count", sa.Integer(), nullable=False, server_default="0")),
        ("unverified_count", sa.Column("unverified_count", sa.Integer(), nullable=False, server_default="0")),
        ("excluded_count", sa.Column("excluded_count", sa.Integer(), nullable=False, server_default="0")),
        ("exclusion_summary", sa.Column("exclusion_summary", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))),
        ("cohort_fingerprint", sa.Column("cohort_fingerprint", sa.String(), nullable=True)),
        ("is_legacy", sa.Column("is_legacy", sa.Boolean(), nullable=False, server_default=sa.text("0" if op.get_bind().dialect.name == "sqlite" else "false"))),
    ]
    for col_name, col_def in market_additions:
        if col_name not in market_cols:
            op.add_column("market_snapshots", col_def)
    if "market_cohort_id" not in market_cols:
        op.create_index("ix_market_snapshots_market_cohort_id", "market_snapshots", ["market_cohort_id"])
    if "category" not in market_cols:
        op.create_index("ix_market_snapshots_category", "market_snapshots", ["category"])

    # 6. Create market_snapshot_members if not exists
    if "market_snapshot_members" not in existing_tables:
        op.create_table(
            "market_snapshot_members",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("market_snapshot_id", sa.Integer(), sa.ForeignKey("market_snapshots.id"), nullable=False),
            sa.Column("listing_snapshot_id", sa.Integer(), sa.ForeignKey("listing_snapshots.id"), nullable=False),
            sa.Column("included", sa.Boolean(), nullable=False, server_default=sa.text("1" if op.get_bind().dialect.name == "sqlite" else "true")),
            sa.Column("role", sa.String(), nullable=False, server_default="active"),
            sa.Column("exclusion_code", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("market_snapshot_id", "listing_snapshot_id", name="uq_market_snapshot_member"),
        )
        op.create_index("ix_market_snapshot_members_market_snapshot_id", "market_snapshot_members", ["market_snapshot_id"])
        op.create_index("ix_market_snapshot_members_listing_snapshot_id", "market_snapshot_members", ["listing_snapshot_id"])

    # 7. Add snapshot and cohort fields to opportunities
    opp_cols = {col["name"] for col in inspector.get_columns("opportunities")}
    opp_additions = [
        ("listing_snapshot_id", sa.Column("listing_snapshot_id", sa.Integer(), nullable=True)),
        ("market_snapshot_id", sa.Column("market_snapshot_id", sa.Integer(), nullable=True)),
        ("market_cohort_id", sa.Column("market_cohort_id", sa.String(), nullable=True)),
        ("category", sa.Column("category", sa.String(), nullable=False, server_default="gpu")),
    ]
    for col_name, col_def in opp_additions:
        if col_name not in opp_cols:
            op.add_column("opportunities", col_def)
    if "listing_snapshot_id" not in opp_cols:
        op.create_index("ix_opportunities_listing_snapshot_id", "opportunities", ["listing_snapshot_id"])
    if "market_snapshot_id" not in opp_cols:
        op.create_index("ix_opportunities_market_snapshot_id", "opportunities", ["market_snapshot_id"])

    # 8. Add snapshot and cohort fields to opportunity_evaluations
    eval_cols = {col["name"] for col in inspector.get_columns("opportunity_evaluations")}
    eval_additions = [
        ("listing_snapshot_id", sa.Column("listing_snapshot_id", sa.Integer(), nullable=True)),
        ("market_snapshot_id", sa.Column("market_snapshot_id", sa.Integer(), nullable=True)),
        ("market_cohort_id", sa.Column("market_cohort_id", sa.String(), nullable=True)),
        ("category", sa.Column("category", sa.String(), nullable=True)),
    ]
    for col_name, col_def in eval_additions:
        if col_name not in eval_cols:
            op.add_column("opportunity_evaluations", col_def)

    # 9. Conservative backfill: mark legacy snapshots without proof as legacy_unverified
    bind = op.get_bind()
    # Mark old market snapshots as legacy
    bind.execute(
        sa.text(
            "UPDATE market_snapshots "
            "SET is_legacy = :legacy "
            "WHERE is_legacy = :not_legacy AND included_count = 0"
        ),
        {"legacy": True, "not_legacy": False},
    )
    # Update listings category from products where possible
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("""
            UPDATE listings SET category = products.category
            FROM products WHERE listings.product_id = products.id AND (listings.category = 'other' OR listings.category IS NULL)
        """))
    else:
        bind.execute(sa.text("""
            UPDATE listings SET category = (
                SELECT category FROM products WHERE products.id = listings.product_id
            ) WHERE product_id IS NOT NULL AND (category IS NULL OR category = 'other')
        """))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())

    if "market_snapshot_members" in existing_tables:
        op.drop_table("market_snapshot_members")

    if "market_cohorts" in existing_tables:
        op.drop_table("market_cohorts")
