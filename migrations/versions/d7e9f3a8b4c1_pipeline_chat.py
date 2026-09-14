"""Add persisted pipeline analysis chat."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7e9f3a8b4c1"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_chat_threads",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("pipeline_run_id", sa.String(), nullable=False),
        sa.Column("next_offset", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pipeline_run_id", name="uq_pipeline_chat_threads_pipeline_run_id"),
    )
    op.create_index("ix_pipeline_chat_threads_pipeline_run_id", "pipeline_chat_threads", ["pipeline_run_id"])

    op.create_table(
        "pipeline_chat_messages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("thread_id", sa.String(), nullable=False),
        sa.Column("client_request_id", sa.String(), nullable=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["thread_id"], ["pipeline_chat_threads.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id", "client_request_id", name="uq_pipeline_chat_messages_thread_request"),
    )
    for column in ("thread_id", "kind", "created_at"):
        op.create_index(f"ix_pipeline_chat_messages_{column}", "pipeline_chat_messages", [column])


def downgrade() -> None:
    for column in ("created_at", "kind", "thread_id"):
        op.drop_index(f"ix_pipeline_chat_messages_{column}", table_name="pipeline_chat_messages")
    op.drop_table("pipeline_chat_messages")
    op.drop_index("ix_pipeline_chat_threads_pipeline_run_id", table_name="pipeline_chat_threads")
    op.drop_table("pipeline_chat_threads")
