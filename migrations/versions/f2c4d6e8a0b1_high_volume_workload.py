"""Persist resumable high-volume marketplace workload state.

Revision ID: f2c4d6e8a0b1
Revises: b5f7c9d1e2a3
"""

from alembic import op
import sqlalchemy as sa


revision = "f2c4d6e8a0b1"
down_revision = "b5f7c9d1e2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ``Base.metadata.create_all`` is intentionally supported by the local
    # seed/bootstrap flow.  If it ran after this code was installed but before
    # Alembic was invoked, the schema is already present while the revision is
    # not yet stamped.  Treat that state as a successful, non-destructive
    # upgrade instead of attempting to recreate its tables.
    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())
    existing_run_columns = {
        column["name"] for column in inspector.get_columns("pipeline_runs")
    }
    high_volume_tables = {
        "pipeline_retrieval_tasks",
        "listing_discoveries",
        "listing_enrichment_tasks",
        "pipeline_navigation_ledger",
    }
    high_volume_columns = {
        "workload_mode",
        "workload_state",
        "workload_not_before",
        "workload_deadline",
    }
    if high_volume_tables.issubset(existing_tables) and high_volume_columns.issubset(existing_run_columns):
        return

    op.add_column(
        "pipeline_runs",
        sa.Column("workload_mode", sa.String(), nullable=False, server_default="standard"),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column("workload_state", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("pipeline_runs", sa.Column("workload_not_before", sa.DateTime(), nullable=True))
    op.add_column("pipeline_runs", sa.Column("workload_deadline", sa.DateTime(), nullable=True))
    op.create_index("ix_pipeline_runs_workload_mode", "pipeline_runs", ["workload_mode"])
    op.create_index("ix_pipeline_runs_workload_not_before", "pipeline_runs", ["workload_not_before"])

    op.create_table(
        "pipeline_retrieval_tasks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("pipeline_run_id", sa.String(), sa.ForeignKey("pipeline_runs.id"), nullable=False),
        sa.Column("pipeline_run_scope_id", sa.String(), sa.ForeignKey("pipeline_run_scopes.id"), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("not_before", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("observed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("discovered_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("pipeline_run_id", "pipeline_run_scope_id", "page", name="uq_retrieval_task_run_scope_page"),
    )
    op.create_index("ix_pipeline_retrieval_tasks_pipeline_run_id", "pipeline_retrieval_tasks", ["pipeline_run_id"])
    op.create_index("ix_pipeline_retrieval_tasks_pipeline_run_scope_id", "pipeline_retrieval_tasks", ["pipeline_run_scope_id"])
    op.create_index("ix_pipeline_retrieval_tasks_status", "pipeline_retrieval_tasks", ["status"])
    op.create_index("ix_pipeline_retrieval_tasks_not_before", "pipeline_retrieval_tasks", ["not_before"])

    op.create_table(
        "listing_discoveries",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("pipeline_run_id", sa.String(), sa.ForeignKey("pipeline_runs.id"), nullable=False),
        sa.Column("pipeline_retrieval_task_id", sa.String(), sa.ForeignKey("pipeline_retrieval_tasks.id"), nullable=True),
        sa.Column("pipeline_run_scope_id", sa.String(), sa.ForeignKey("pipeline_run_scopes.id"), nullable=True),
        sa.Column("canonical_key", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("marketplace_item_id", sa.String(), nullable=True),
        sa.Column("normalized_url", sa.String(), nullable=False, server_default=""),
        sa.Column("title", sa.String(), nullable=False, server_default=""),
        sa.Column("price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("location", sa.String(), nullable=False, server_default=""),
        sa.Column("condition", sa.String(), nullable=False, server_default=""),
        sa.Column("seller", sa.String(), nullable=False, server_default=""),
        sa.Column("raw_summary", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("triage_status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("triage_reason", sa.String(), nullable=False, server_default=""),
        sa.Column("priority_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("first_observed_at", sa.DateTime(), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("pipeline_run_id", "canonical_key", name="uq_listing_discovery_run_canonical"),
    )
    for column in ("pipeline_run_id", "pipeline_retrieval_task_id", "marketplace_item_id", "triage_status", "priority_score"):
        op.create_index(f"ix_listing_discoveries_{column}", "listing_discoveries", [column])

    op.create_table(
        "listing_enrichment_tasks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("pipeline_run_id", sa.String(), sa.ForeignKey("pipeline_runs.id"), nullable=False),
        sa.Column("listing_discovery_id", sa.String(), sa.ForeignKey("listing_discoveries.id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("not_before", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("listing_id", sa.String(), sa.ForeignKey("listings.id"), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("pipeline_run_id", "listing_discovery_id", name="uq_enrichment_task_run_discovery"),
    )
    for column in ("pipeline_run_id", "listing_discovery_id", "status", "not_before", "listing_id"):
        op.create_index(f"ix_listing_enrichment_tasks_{column}", "listing_enrichment_tasks", [column])

    op.create_table(
        "pipeline_navigation_ledger",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("pipeline_run_id", sa.String(), sa.ForeignKey("pipeline_runs.id"), nullable=False),
        sa.Column("task_kind", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="completed"),
        sa.Column("scheduled_at", sa.DateTime(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("pipeline_run_id", "task_kind", "task_id", name="uq_navigation_ledger_task"),
    )
    op.create_index("ix_pipeline_navigation_ledger_pipeline_run_id", "pipeline_navigation_ledger", ["pipeline_run_id"])
    op.create_index("ix_pipeline_navigation_ledger_status", "pipeline_navigation_ledger", ["status"])


def downgrade() -> None:
    op.drop_table("pipeline_navigation_ledger")
    op.drop_table("listing_enrichment_tasks")
    op.drop_table("listing_discoveries")
    op.drop_table("pipeline_retrieval_tasks")
    op.drop_index("ix_pipeline_runs_workload_not_before", table_name="pipeline_runs")
    op.drop_index("ix_pipeline_runs_workload_mode", table_name="pipeline_runs")
    op.drop_column("pipeline_runs", "workload_deadline")
    op.drop_column("pipeline_runs", "workload_not_before")
    op.drop_column("pipeline_runs", "workload_state")
    op.drop_column("pipeline_runs", "workload_mode")
