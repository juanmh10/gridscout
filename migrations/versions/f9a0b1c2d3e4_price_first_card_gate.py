"""Persist price-first card gate decisions and semantic-contract provenance.

Revision ID: f9a0b1c2d3e4
Revises: c9e0f1a2b3c4
"""

from alembic import op
import sqlalchemy as sa


revision = "f9a0b1c2d3e4"
down_revision = "c9e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("listing_discoveries")}
    additions = (
        ("price_raw", sa.Column("price_raw", sa.String(), nullable=False, server_default="")),
        ("card_analysis_contract_version", sa.Column("card_analysis_contract_version", sa.String(), nullable=True)),
        ("card_analysis_evidence_hash", sa.Column("card_analysis_evidence_hash", sa.String(), nullable=True)),
        ("gate_status", sa.Column("gate_status", sa.String(), nullable=False, server_default="observed")),
        ("gate_reason_code", sa.Column("gate_reason_code", sa.String(), nullable=True)),
        ("gate_evidence", sa.Column("gate_evidence", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))),
        ("gate_evidence_hash", sa.Column("gate_evidence_hash", sa.String(), nullable=True)),
    )
    for name, column in additions:
        if name not in columns:
            op.add_column("listing_discoveries", column)
    indexes = {index["name"] for index in inspector.get_indexes("listing_discoveries")}
    for name, index_columns in (
        ("ix_listing_discoveries_card_analysis_evidence_hash", ["card_analysis_evidence_hash"]),
        ("ix_listing_discoveries_gate_status", ["gate_status"]),
        ("ix_listing_discoveries_gate_reason_code", ["gate_reason_code"]),
        ("ix_listing_discoveries_gate_evidence_hash", ["gate_evidence_hash"]),
    ):
        if name not in indexes:
            op.create_index(name, "listing_discoveries", index_columns)


def downgrade() -> None:
    for name in (
        "ix_listing_discoveries_gate_evidence_hash",
        "ix_listing_discoveries_gate_reason_code",
        "ix_listing_discoveries_gate_status",
        "ix_listing_discoveries_card_analysis_evidence_hash",
    ):
        op.drop_index(name, table_name="listing_discoveries")
    for name in (
        "gate_evidence_hash", "gate_evidence", "gate_reason_code", "gate_status",
        "card_analysis_evidence_hash", "card_analysis_contract_version", "price_raw",
    ):
        op.drop_column("listing_discoveries", name)
