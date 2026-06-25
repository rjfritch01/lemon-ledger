"""Create lot tracking tables: tax_lots, lot_disposals, lot_relocations, lot_processing_exceptions.

Revision ID: b3c4d5e6f7a2
Revises: a2b3c4d5e6f7
Create Date: 2026-06-24 00:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3c4d5e6f7a2"
down_revision: str | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ASSET_CLASSES = "'fungible','collectible'"
_HOLDING_PERIODS = "'short','long'"
_ACQ_TYPES = "'buy','mint','reward','bridge-in','gift','cap-contribution'"
_SEL_STRATEGIES = "'fifo','hifo','lifo','manual'"
_EXCEPTION_REASONS = "'insufficient_lots','missing_basis','unresolved_fee'"
_RELOCATION_REASONS = "'wrap','unwrap','bridge','cap-contribution','gift','loan'"


def upgrade() -> None:
    # ── tax_lots ──────────────────────────────────────────────────────────────
    op.create_table(
        "tax_lots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("wallet_id", sa.Uuid(), nullable=False),
        sa.Column("acquired_token_id", sa.Uuid(), nullable=False),
        sa.Column("logical_asset_id", sa.Uuid(), nullable=True),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acquisition_type", sa.Text(), nullable=False),
        sa.Column("asset_class", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(78, 18), nullable=False),
        sa.Column("quantity_remaining", sa.Numeric(78, 18), nullable=False),
        sa.Column("cost_basis_usd", sa.Numeric(38, 18), nullable=False),
        sa.Column("source_classified_tx_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_classified_tx_id", name="uq_tax_lots_source_classified_tx"
        ),
        sa.ForeignKeyConstraint(["wallet_id"], ["wallets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["acquired_token_id"], ["token_registry.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["logical_asset_id"], ["logical_assets.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_classified_tx_id"],
            ["classified_transactions.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"asset_class IN ({_ASSET_CLASSES})", name="ck_tax_lots_asset_class"
        ),
        sa.CheckConstraint(
            f"acquisition_type IN ({_ACQ_TYPES})", name="ck_tax_lots_acquisition_type"
        ),
        sa.CheckConstraint(
            "quantity_remaining >= 0 AND quantity_remaining <= quantity",
            name="ck_tax_lots_quantity_remaining",
        ),
    )
    op.create_index("ix_tax_lots_wallet_logical", "tax_lots", ["wallet_id", "logical_asset_id"])
    op.create_index("ix_tax_lots_wallet_token", "tax_lots", ["wallet_id", "acquired_token_id"])
    op.create_index("ix_tax_lots_acquired_at", "tax_lots", ["acquired_at"])

    # ── lot_disposals ─────────────────────────────────────────────────────────
    op.create_table(
        "lot_disposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=False),
        sa.Column("disposal_tx_id", sa.Uuid(), nullable=False),
        sa.Column("quantity_consumed", sa.Numeric(78, 18), nullable=False),
        sa.Column(
            "proceeds_usd",
            sa.Numeric(38, 18),
            nullable=False,
            server_default="0",
        ),
        sa.Column("basis_consumed_usd", sa.Numeric(38, 18), nullable=False),
        sa.Column("gain_loss_usd", sa.Numeric(38, 18), nullable=False),
        sa.Column("holding_period", sa.Text(), nullable=False),
        sa.Column("asset_class", sa.Text(), nullable=False),
        sa.Column("selection_strategy", sa.Text(), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("disposal_tx_id", "lot_id", name="uq_lot_disposals_tx_lot"),
        sa.ForeignKeyConstraint(["lot_id"], ["tax_lots.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["disposal_tx_id"],
            ["classified_transactions.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"holding_period IN ({_HOLDING_PERIODS})",
            name="ck_lot_disposals_holding_period",
        ),
        sa.CheckConstraint(
            f"asset_class IN ({_ASSET_CLASSES})", name="ck_lot_disposals_asset_class"
        ),
        sa.CheckConstraint(
            f"selection_strategy IN ({_SEL_STRATEGIES})",
            name="ck_lot_disposals_selection_strategy",
        ),
    )

    # ── lot_relocations ───────────────────────────────────────────────────────
    op.create_table(
        "lot_relocations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=False),
        sa.Column("from_wallet_id", sa.Uuid(), nullable=False),
        sa.Column("to_wallet_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("classified_tx_id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["lot_id"], ["tax_lots.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["from_wallet_id"], ["wallets.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["to_wallet_id"], ["wallets.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["classified_tx_id"],
            ["classified_transactions.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"reason IN ({_RELOCATION_REASONS})", name="ck_lot_relocations_reason"
        ),
    )

    # ── lot_processing_exceptions ─────────────────────────────────────────────
    op.create_table(
        "lot_processing_exceptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("classified_tx_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("quantity_unmatched", sa.Numeric(78, 18), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["classified_tx_id"],
            ["classified_transactions.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"reason IN ({_EXCEPTION_REASONS})", name="ck_lot_exc_reason"
        ),
    )


def downgrade() -> None:
    op.drop_table("lot_processing_exceptions")
    op.drop_table("lot_relocations")
    op.drop_table("lot_disposals")
    op.drop_index("ix_tax_lots_acquired_at", table_name="tax_lots")
    op.drop_index("ix_tax_lots_wallet_token", table_name="tax_lots")
    op.drop_index("ix_tax_lots_wallet_logical", table_name="tax_lots")
    op.drop_table("tax_lots")
