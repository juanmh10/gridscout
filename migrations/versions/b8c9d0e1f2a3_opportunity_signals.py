"""Add card-only opportunity signals.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""

from alembic import op
import sqlalchemy as sa


revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "opportunity_signals" not in inspector.get_table_names():
        op.create_table(
            "opportunity_signals",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("profile_id", sa.String(), nullable=False),
            sa.Column("pipeline_run_id", sa.String(), nullable=False),
            sa.Column("listing_discovery_id", sa.String(), nullable=False),
            sa.Column("opportunity_id", sa.String(), nullable=True),
            sa.Column("stage", sa.String(), nullable=False, server_default="preliminary"),
            sa.Column("preliminary_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("score_breakdown", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("scorer_version", sa.String(), nullable=False, server_default="card-signal-v1"),
            sa.Column("eligibility_reasons", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("full_flow_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"]),
            sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"]),
            sa.ForeignKeyConstraint(["listing_discovery_id"], ["listing_discoveries.id"]),
            sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("profile_id", "listing_discovery_id", name="uq_signal_profile_discovery"),
        )
    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("opportunity_signals")}
    for column in ("profile_id", "pipeline_run_id", "listing_discovery_id", "opportunity_id", "stage", "preliminary_score", "full_flow_completed"):
        name = f"ix_opportunity_signals_{column}"
        if name not in indexes:
            op.create_index(name, "opportunity_signals", [column])


def downgrade():
    op.drop_table("opportunity_signals")
