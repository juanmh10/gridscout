"""Persist agent-derived evidence for incomplete discovery cards.

Revision ID: c6f8a2d9e4b7
Revises: a9d4e2c7b6f1
"""

from alembic import op
import sqlalchemy as sa


revision = "c6f8a2d9e4b7"
down_revision = "a9d4e2c7b6f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("listing_discoveries")}
    additions = (
        ("card_analysis_status", sa.Column("card_analysis_status", sa.String(), nullable=False, server_default="not_requested")),
        ("card_analysis_result", sa.Column("card_analysis_result", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))),
        ("card_analysis_model_id", sa.Column("card_analysis_model_id", sa.String(), nullable=True)),
        ("card_analysis_error", sa.Column("card_analysis_error", sa.Text(), nullable=True)),
        ("card_analysis_attempts", sa.Column("card_analysis_attempts", sa.Integer(), nullable=False, server_default="0")),
        ("card_analysis_updated_at", sa.Column("card_analysis_updated_at", sa.DateTime(), nullable=True)),
    )
    for name, column in additions:
        if name not in columns:
            op.add_column("listing_discoveries", column)
    indexes = {index["name"] for index in inspector.get_indexes("listing_discoveries")}
    if "ix_listing_discoveries_card_analysis_status" not in indexes:
        op.create_index("ix_listing_discoveries_card_analysis_status", "listing_discoveries", ["card_analysis_status"])


def downgrade() -> None:
    op.drop_index("ix_listing_discoveries_card_analysis_status", table_name="listing_discoveries")
    op.drop_column("listing_discoveries", "card_analysis_updated_at")
    op.drop_column("listing_discoveries", "card_analysis_attempts")
    op.drop_column("listing_discoveries", "card_analysis_error")
    op.drop_column("listing_discoveries", "card_analysis_model_id")
    op.drop_column("listing_discoveries", "card_analysis_result")
    op.drop_column("listing_discoveries", "card_analysis_status")
