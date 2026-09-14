"""Align denormalized opportunity categories with canonical products.

Revision ID: f4c6e8a1b3d5
Revises: d3e5f7a9c1b2
"""

from alembic import op
import sqlalchemy as sa


revision = "f4c6e8a1b3d5"
down_revision = "d3e5f7a9c1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Repair rows written before category propagation was enforced.

    ``opportunities.category`` is retained for compatibility, but Product is
    the canonical category owner.  Older rows inherited the model default
    ``gpu`` and could therefore disappear from the Notebook filter.
    """
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                "UPDATE opportunities AS opportunity "
                "SET category = product.category "
                "FROM products AS product "
                "WHERE opportunity.product_id = product.id "
                "AND (opportunity.category IS NULL OR opportunity.category <> product.category)"
            )
        )
        bind.execute(
            sa.text(
                "UPDATE opportunities AS opportunity "
                "SET is_current = false, investigation_status = 'not_qualified' "
                "FROM products AS product "
                "WHERE opportunity.product_id = product.id AND product.category = 'other'"
            )
        )
        return

    bind.execute(
        sa.text(
            "UPDATE opportunities "
            "SET category = (SELECT category FROM products WHERE products.id = opportunities.product_id) "
            "WHERE EXISTS ("
            "SELECT 1 FROM products "
            "WHERE products.id = opportunities.product_id "
            "AND (opportunities.category IS NULL OR opportunities.category <> products.category)"
            ")"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE opportunities SET is_current = 0, investigation_status = 'not_qualified' "
            "WHERE product_id IN (SELECT id FROM products WHERE category = 'other')"
        )
    )


def downgrade() -> None:
    # This repairs denormalized data and deliberately has no destructive undo.
    pass
