"""Allow nullable price in listing_discoveries for unpriced cards.

Revision ID: a7b8c9d0e1f2
Revises: f4c6e8a1b3d5
"""

from alembic import op
import sqlalchemy as sa


revision = "a7b8c9d0e1f2"
down_revision = "f4c6e8a1b3d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.alter_column("listing_discoveries", "price", existing_type=sa.Float(), nullable=True)
    else:
        with op.batch_alter_table("listing_discoveries") as batch_op:
            batch_op.alter_column("price", existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.alter_column("listing_discoveries", "price", existing_type=sa.Float(), nullable=False, server_default="0")
    else:
        with op.batch_alter_table("listing_discoveries") as batch_op:
            batch_op.alter_column("price", existing_type=sa.Float(), nullable=False, server_default="0")
