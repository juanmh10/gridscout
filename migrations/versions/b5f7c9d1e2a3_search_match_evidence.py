"""Persist per-scope search match classification and evidence.

Revision ID: b5f7c9d1e2a3
Revises: e8f1a2b3c4d5
"""

from alembic import op
import sqlalchemy as sa


revision = "b5f7c9d1e2a3"
down_revision = "e8f1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pipeline_run_scope_items", sa.Column("match_status", sa.String(), nullable=True))
    op.add_column("pipeline_run_scope_items", sa.Column("match_details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_index("ix_pipeline_run_scope_items_match_status", "pipeline_run_scope_items", ["match_status"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_run_scope_items_match_status", table_name="pipeline_run_scope_items")
    op.drop_column("pipeline_run_scope_items", "match_details")
    op.drop_column("pipeline_run_scope_items", "match_status")
