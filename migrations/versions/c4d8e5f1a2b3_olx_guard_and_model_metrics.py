"""Add model-call telemetry and structured pipeline errors."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c4d8e5f1a2b3"
down_revision: Union[str, Sequence[str], None] = "9c1a8b2d4e6f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pipeline_runs", sa.Column("error_code", sa.String(), nullable=True))
    op.create_table(
        "model_metric_calls",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("pipeline_run_id", sa.String(), nullable=True),
        sa.Column("origin", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("auth_mode", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("candidates_tokens", sa.Integer(), nullable=False),
        sa.Column("thoughts_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_tokens", sa.Integer(), nullable=False),
        sa.Column("tool_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("finish_reason", sa.String(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("pricing_version", sa.String(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("pipeline_run_id", "origin", "operation", "model_id", "status", "started_at"):
        op.create_index(f"ix_model_metric_calls_{column}", "model_metric_calls", [column])


def downgrade() -> None:
    for column in ("started_at", "status", "model_id", "operation", "origin", "pipeline_run_id"):
        op.drop_index(f"ix_model_metric_calls_{column}", table_name="model_metric_calls")
    op.drop_table("model_metric_calls")
    op.drop_column("pipeline_runs", "error_code")
