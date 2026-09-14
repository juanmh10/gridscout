"""Add card-level listing projections and preliminary market tiers.

Revision ID: c9e0f1a2b3c4
Revises: b8c9d0e1f2a3
"""

from alembic import op
import sqlalchemy as sa


revision = "c9e0f1a2b3c4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("listings") as batch:
        batch.alter_column("price", existing_type=sa.Float(), nullable=True)
        batch.alter_column("location_state", existing_type=sa.String(), nullable=True)
        batch.alter_column("location_city", existing_type=sa.String(), nullable=True)
        batch.alter_column("seller_name", existing_type=sa.String(), nullable=True)
        batch.alter_column("seller_rating", existing_type=sa.Float(), nullable=True)
        batch.alter_column("condition", existing_type=sa.String(), nullable=True)
        batch.add_column(sa.Column("evidence_level", sa.String(), nullable=False, server_default="detail"))
        batch.add_column(sa.Column("audit_status", sa.String(), nullable=False, server_default="audited"))
        batch.add_column(sa.Column("evidence_provenance", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch.add_column(sa.Column("last_audited_at", sa.DateTime(), nullable=True))
        batch.create_index("ix_listings_evidence_level", ["evidence_level"])
        batch.create_index("ix_listings_audit_status", ["audit_status"])

    with op.batch_alter_table("listing_snapshots") as batch:
        batch.alter_column("price", existing_type=sa.Float(), nullable=True)
        batch.add_column(sa.Column("evidence_level", sa.String(), nullable=False, server_default="detail"))
        batch.add_column(sa.Column("listing_discovery_id", sa.String(), nullable=True))
        batch.create_foreign_key("fk_listing_snapshots_discovery", "listing_discoveries", ["listing_discovery_id"], ["id"])
        batch.create_index("ix_listing_snapshots_evidence_level", ["evidence_level"])
        batch.create_index("ix_listing_snapshots_discovery", ["listing_discovery_id"])

    with op.batch_alter_table("market_snapshots") as batch:
        batch.add_column(sa.Column("evidence_tier", sa.String(), nullable=False, server_default="audited"))
        batch.create_index("ix_market_snapshots_evidence_tier", ["evidence_tier"])

    with op.batch_alter_table("listing_discoveries") as batch:
        batch.add_column(sa.Column("rule_analysis_status", sa.String(), nullable=False, server_default="pending"))
        batch.add_column(sa.Column("rule_analysis_result", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch.add_column(sa.Column("rule_version", sa.String(), nullable=True))
        batch.add_column(sa.Column("publication_status", sa.String(), nullable=False, server_default="observed"))
        batch.add_column(sa.Column("publication_reason", sa.String(), nullable=False, server_default=""))
        batch.add_column(sa.Column("listing_id", sa.String(), nullable=True))
        batch.create_foreign_key("fk_listing_discoveries_listing", "listings", ["listing_id"], ["id"])
        batch.create_index("ix_listing_discoveries_rule_status", ["rule_analysis_status"])
        batch.create_index("ix_listing_discoveries_publication_status", ["publication_status"])
        batch.create_index("ix_listing_discoveries_listing", ["listing_id"])


def downgrade() -> None:
    with op.batch_alter_table("listing_discoveries") as batch:
        batch.drop_index("ix_listing_discoveries_listing")
        batch.drop_index("ix_listing_discoveries_publication_status")
        batch.drop_index("ix_listing_discoveries_rule_status")
        batch.drop_constraint("fk_listing_discoveries_listing", type_="foreignkey")
        batch.drop_column("listing_id")
        batch.drop_column("publication_reason")
        batch.drop_column("publication_status")
        batch.drop_column("rule_version")
        batch.drop_column("rule_analysis_result")
        batch.drop_column("rule_analysis_status")
    with op.batch_alter_table("market_snapshots") as batch:
        batch.drop_index("ix_market_snapshots_evidence_tier")
        batch.drop_column("evidence_tier")
    with op.batch_alter_table("listing_snapshots") as batch:
        batch.drop_index("ix_listing_snapshots_discovery")
        batch.drop_index("ix_listing_snapshots_evidence_level")
        batch.drop_constraint("fk_listing_snapshots_discovery", type_="foreignkey")
        batch.drop_column("listing_discovery_id")
        batch.drop_column("evidence_level")
        batch.alter_column("price", existing_type=sa.Float(), nullable=False)
    with op.batch_alter_table("listings") as batch:
        batch.drop_index("ix_listings_audit_status")
        batch.drop_index("ix_listings_evidence_level")
        batch.drop_column("last_audited_at")
        batch.drop_column("evidence_provenance")
        batch.drop_column("audit_status")
        batch.drop_column("evidence_level")
        batch.alter_column("condition", existing_type=sa.String(), nullable=False)
        batch.alter_column("seller_rating", existing_type=sa.Float(), nullable=False)
        batch.alter_column("seller_name", existing_type=sa.String(), nullable=False)
        batch.alter_column("location_city", existing_type=sa.String(), nullable=False)
        batch.alter_column("location_state", existing_type=sa.String(), nullable=False)
        batch.alter_column("price", existing_type=sa.Float(), nullable=False)
