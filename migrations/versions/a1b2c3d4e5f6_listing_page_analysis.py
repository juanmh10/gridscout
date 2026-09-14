"""Add page-scoped listing analysis and delivery/seller signals."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "c4d8e5f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on = None


def upgrade() -> None:
    op.add_column("listings", sa.Column("delivery_status", sa.String(), nullable=True, server_default="UNKNOWN"))
    op.add_column("listings", sa.Column("delivery_evidence", sa.JSON(), nullable=True))
    op.add_column("listings", sa.Column("seller_verification", sa.String(), nullable=True, server_default="UNKNOWN"))
    op.add_column("listings", sa.Column("seller_signal_level", sa.String(), nullable=True, server_default="UNKNOWN"))
    op.add_column("listings", sa.Column("seller_evidence", sa.JSON(), nullable=True))
    op.add_column("listings", sa.Column("analysis_summary", sa.Text(), nullable=True, server_default=""))
    op.add_column("listings", sa.Column("analysis_status", sa.String(), nullable=True, server_default="unavailable"))
    op.add_column("listings", sa.Column("analysis_updated_at", sa.DateTime(), nullable=True))
    for column in ("delivery_status", "seller_verification", "seller_signal_level", "analysis_status"):
        op.create_index(f"ix_listings_{column}", "listings", [column])

    op.create_table(
        "listing_analyses",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("listing_id", sa.String(), nullable=False),
        sa.Column("pipeline_run_id", sa.String(), nullable=True),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("photos_examined", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("external_navigation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"]),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("listing_id", "pipeline_run_id", "stage", "status"):
        op.create_index(f"ix_listing_analyses_{column}", "listing_analyses", [column])


def downgrade() -> None:
    for column in ("status", "stage", "pipeline_run_id", "listing_id"):
        op.drop_index(f"ix_listing_analyses_{column}", table_name="listing_analyses")
    op.drop_table("listing_analyses")
    for column in ("analysis_status", "seller_signal_level", "seller_verification", "delivery_status"):
        op.drop_index(f"ix_listings_{column}", table_name="listings")
    for column in (
        "analysis_updated_at",
        "analysis_status",
        "analysis_summary",
        "seller_evidence",
        "seller_signal_level",
        "seller_verification",
        "delivery_evidence",
        "delivery_status",
    ):
        op.drop_column("listings", column)
