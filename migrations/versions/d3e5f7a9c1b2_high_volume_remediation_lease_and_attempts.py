"""High-volume diagnostic remediation: attempts, transitions, leases, parser artifacts, and metric telemetry.

Revision ID: d3e5f7a9c1b2
Revises: e1f2a3b4c5d6
"""

from alembic import op
import sqlalchemy as sa


revision = "d3e5f7a9c1b2"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())

    # 1. Create pipeline_workload_attempts
    if "pipeline_workload_attempts" not in existing_tables:
        op.create_table(
            "pipeline_workload_attempts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("pipeline_run_id", sa.String(), sa.ForeignKey("pipeline_runs.id"), nullable=False),
            sa.Column("attempt_number", sa.Integer(), nullable=False),
            sa.Column("reason", sa.String(), nullable=False, server_default="initial"),
            sa.Column("status", sa.String(), nullable=False, server_default="running"),
            sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("deadline_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("worker_revision", sa.String(), nullable=True),
            sa.Column("parser_version", sa.String(), nullable=True),
            sa.Column("error_code", sa.String(), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.UniqueConstraint("pipeline_run_id", "attempt_number", name="uq_pipeline_workload_attempt_run_number"),
        )
        op.create_index("ix_pipeline_workload_attempts_pipeline_run_id", "pipeline_workload_attempts", ["pipeline_run_id"])
        op.create_index("ix_pipeline_workload_attempts_reason", "pipeline_workload_attempts", ["reason"])
        op.create_index("ix_pipeline_workload_attempts_status", "pipeline_workload_attempts", ["status"])

    # 2. Create pipeline_state_transitions
    if "pipeline_state_transitions" not in existing_tables:
        op.create_table(
            "pipeline_state_transitions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("pipeline_run_id", sa.String(), sa.ForeignKey("pipeline_runs.id"), nullable=False),
            sa.Column("from_status", sa.String(), nullable=False),
            sa.Column("to_status", sa.String(), nullable=False),
            sa.Column("reason_code", sa.String(), nullable=False),
            sa.Column("occurred_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("actor", sa.String(), nullable=False, server_default="scheduler"),
            sa.Column("message", sa.Text(), nullable=True),
        )
        op.create_index("ix_pipeline_state_transitions_pipeline_run_id", "pipeline_state_transitions", ["pipeline_run_id"])
        op.create_index("ix_pipeline_state_transitions_occurred_at", "pipeline_state_transitions", ["occurred_at"])

    # 3. Create pipeline_parser_artifacts
    if "pipeline_parser_artifacts" not in existing_tables:
        op.create_table(
            "pipeline_parser_artifacts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("pipeline_run_id", sa.String(), sa.ForeignKey("pipeline_runs.id"), nullable=True),
            sa.Column("task_id", sa.String(), nullable=True),
            sa.Column("attempt_number", sa.Integer(), nullable=True),
            sa.Column("parser_version", sa.String(), nullable=False),
            sa.Column("build_sha", sa.String(), nullable=True),
            sa.Column("stage", sa.String(), nullable=False, server_default="detail"),
            sa.Column("url_hash", sa.String(), nullable=False),
            sa.Column("page_state", sa.String(), nullable=False, server_default="unknown"),
            sa.Column("http_status", sa.Integer(), nullable=True),
            sa.Column("missing_fields", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("selectors_tried", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("provenance", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("sanitized_snippet", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_pipeline_parser_artifacts_pipeline_run_id", "pipeline_parser_artifacts", ["pipeline_run_id"])
        op.create_index("ix_pipeline_parser_artifacts_task_id", "pipeline_parser_artifacts", ["task_id"])
        op.create_index("ix_pipeline_parser_artifacts_url_hash", "pipeline_parser_artifacts", ["url_hash"])
        op.create_index("ix_pipeline_parser_artifacts_stage", "pipeline_parser_artifacts", ["stage"])

    # 4. Update pipeline_retrieval_tasks with lease columns
    if "pipeline_retrieval_tasks" in existing_tables:
        retrieval_cols = {col["name"] for col in inspector.get_columns("pipeline_retrieval_tasks")}
        retrieval_additions = [
            ("max_attempts", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3")),
            ("lease_owner", sa.Column("lease_owner", sa.String(), nullable=True)),
            ("lease_token", sa.Column("lease_token", sa.String(), nullable=True)),
            ("lease_expires_at", sa.Column("lease_expires_at", sa.DateTime(), nullable=True)),
            ("heartbeat_at", sa.Column("heartbeat_at", sa.DateTime(), nullable=True)),
            ("last_error_code", sa.Column("last_error_code", sa.String(), nullable=True)),
            ("last_error_at", sa.Column("last_error_at", sa.DateTime(), nullable=True)),
        ]
        for col_name, col_def in retrieval_additions:
            if col_name not in retrieval_cols:
                op.add_column("pipeline_retrieval_tasks", col_def)
        if "lease_owner" not in retrieval_cols:
            op.create_index("ix_pipeline_retrieval_tasks_lease_owner", "pipeline_retrieval_tasks", ["lease_owner"])
        if "lease_expires_at" not in retrieval_cols:
            op.create_index("ix_pipeline_retrieval_tasks_lease_expires_at", "pipeline_retrieval_tasks", ["lease_expires_at"])

    # 5. Update listing_enrichment_tasks with lease columns
    if "listing_enrichment_tasks" in existing_tables:
        enrichment_cols = {col["name"] for col in inspector.get_columns("listing_enrichment_tasks")}
        enrichment_additions = [
            ("max_attempts", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3")),
            ("lease_owner", sa.Column("lease_owner", sa.String(), nullable=True)),
            ("lease_token", sa.Column("lease_token", sa.String(), nullable=True)),
            ("lease_expires_at", sa.Column("lease_expires_at", sa.DateTime(), nullable=True)),
            ("heartbeat_at", sa.Column("heartbeat_at", sa.DateTime(), nullable=True)),
            ("last_error_code", sa.Column("last_error_code", sa.String(), nullable=True)),
            ("last_error_at", sa.Column("last_error_at", sa.DateTime(), nullable=True)),
        ]
        for col_name, col_def in enrichment_additions:
            if col_name not in enrichment_cols:
                op.add_column("listing_enrichment_tasks", col_def)
        if "lease_owner" not in enrichment_cols:
            op.create_index("ix_listing_enrichment_tasks_lease_owner", "listing_enrichment_tasks", ["lease_owner"])
        if "lease_expires_at" not in enrichment_cols:
            op.create_index("ix_listing_enrichment_tasks_lease_expires_at", "listing_enrichment_tasks", ["lease_expires_at"])

    # 6. Update listing_discoveries with provenance and parser_version columns
    if "listing_discoveries" in existing_tables:
        discovery_cols = {col["name"] for col in inspector.get_columns("listing_discoveries")}
        discovery_additions = [
            ("price_origin", sa.Column("price_origin", sa.String(), nullable=False, server_default="dom")),
            ("location_origin", sa.Column("location_origin", sa.String(), nullable=False, server_default="dom")),
            ("field_coverage", sa.Column("field_coverage", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))),
            ("parser_version", sa.Column("parser_version", sa.String(), nullable=False, server_default="olx-dom-2026-08-24")),
        ]
        for col_name, col_def in discovery_additions:
            if col_name not in discovery_cols:
                op.add_column("listing_discoveries", col_def)

    # 7. Update model_metric_calls with attempt/task/quality columns
    if "model_metric_calls" in existing_tables:
        metric_cols = {col["name"] for col in inspector.get_columns("model_metric_calls")}
        metric_additions = [
            ("attempt_number", sa.Column("attempt_number", sa.Integer(), nullable=True)),
            ("task_kind", sa.Column("task_kind", sa.String(), nullable=True)),
            ("task_id", sa.Column("task_id", sa.String(), nullable=True)),
            ("normalization_quality", sa.Column("normalization_quality", sa.String(), nullable=True)),
            ("response_status", sa.Column("response_status", sa.String(), nullable=True)),
            ("provider_status", sa.Column("provider_status", sa.String(), nullable=True)),
        ]
        for col_name, col_def in metric_additions:
            if col_name not in metric_cols:
                op.add_column("model_metric_calls", col_def)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())

    if "pipeline_parser_artifacts" in existing_tables:
        op.drop_table("pipeline_parser_artifacts")
    if "pipeline_state_transitions" in existing_tables:
        op.drop_table("pipeline_state_transitions")
    if "pipeline_workload_attempts" in existing_tables:
        op.drop_table("pipeline_workload_attempts")
