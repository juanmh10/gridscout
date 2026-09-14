"""Persist data-only analysis for a high-volume discovery dataset.

Revision ID: a9d4e2c7b6f1
Revises: f2c4d6e8a0b1
"""

from alembic import op
import sqlalchemy as sa


revision = "a9d4e2c7b6f1"
down_revision = "f2c4d6e8a0b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "pipeline_dataset_analyses" in set(inspector.get_table_names()):
        return

    op.create_table(
        "pipeline_dataset_analyses",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("pipeline_run_id", sa.String(), sa.ForeignKey("pipeline_runs.id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("schema_version", sa.String(), nullable=False, server_default="high-volume-dataset-analysis-v1"),
        sa.Column("result", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("agent_status", sa.String(), nullable=False, server_default="not_requested"),
        sa.Column("agent_model_id", sa.String(), nullable=True),
        sa.Column("agent_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("agent_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("pipeline_run_id", name="uq_pipeline_dataset_analysis_run"),
    )
    op.create_index("ix_pipeline_dataset_analyses_pipeline_run_id", "pipeline_dataset_analyses", ["pipeline_run_id"])
    op.create_index("ix_pipeline_dataset_analyses_status", "pipeline_dataset_analyses", ["status"])
    op.create_index("ix_pipeline_dataset_analyses_agent_status", "pipeline_dataset_analyses", ["agent_status"])


def downgrade() -> None:
    op.drop_table("pipeline_dataset_analyses")
