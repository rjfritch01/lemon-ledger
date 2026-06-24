"""classified_transactions, l2_decoder_config, wallet.last_classified_block

Revision ID: b2c3d4e5f6a1
Revises: a1b2c3d4e5f6
Create Date: 2026-06-23 00:01:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2c3d4e5f6a1"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Derived from ClassificationKind at migration time so the CHECK never drifts.
_KINDS = (
    "'reward','mint','stake','unstake','transfer-in','transfer-out','unclassified'"
)


def upgrade() -> None:
    # ── classified_transactions ────────────────────────────────────────────────
    op.create_table(
        "classified_transactions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "wallet_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wallets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("tx_hash", sa.String(66), nullable=False),
        sa.Column("event_seq", sa.SmallInteger(), nullable=False),
        sa.Column("block_number", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column(
            "token_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("token_registry.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("contract_address", sa.String(42), nullable=False),
        sa.Column("amount", sa.Numeric(78, 18), nullable=False),
        sa.Column("value_usd_at_event", sa.Numeric(38, 18), nullable=True),
        sa.Column(
            "needs_review", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "manual_override", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "related_lots",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=True,
        ),
        sa.Column(
            "bridge_correlation_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "wallet_id",
            "tx_hash",
            "event_seq",
            name="uq_classified_wallet_tx_seq",
        ),
        sa.CheckConstraint(
            f"classification IN ({_KINDS})",
            name="ck_classification_kind",
        ),
        sa.CheckConstraint(
            "chain IN ('lemonchain','bsc')",
            name="ck_classified_chain",
        ),
    )
    op.create_index(
        "ix_classified_wallet_block",
        "classified_transactions",
        ["wallet_id", "block_number"],
    )
    op.create_index(
        "ix_classified_contract_address",
        "classified_transactions",
        ["contract_address"],
    )

    # ── l2_decoder_config ──────────────────────────────────────────────────────
    op.create_table(
        "l2_decoder_config",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "token_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("token_registry.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("chain", sa.String(32), nullable=False),
        sa.Column("decoder_class", sa.String(64), nullable=False),
        sa.Column("nft_contract", sa.String(42), nullable=True),
        sa.Column("staking_contract", sa.String(42), nullable=True),
        sa.Column("mint_contract", sa.String(42), nullable=True),
        sa.Column(
            "nft_contract_status",
            sa.String(16),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column(
            "staking_contract_status",
            sa.String(16),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("mint_fee_wei", sa.Numeric(78, 0), nullable=True),
        sa.Column("reward_event_topic0", sa.String(66), nullable=True),
        sa.Column(
            "burn_and_mint", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "distribution_complete",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "chain IN ('lemonchain','bsc')", name="ck_l2config_chain"
        ),
        sa.CheckConstraint(
            "nft_contract_status IN ('unknown','discovered','confirmed')",
            name="ck_l2config_nft_status",
        ),
        sa.CheckConstraint(
            "staking_contract_status IN ('unknown','discovered','confirmed')",
            name="ck_l2config_staking_status",
        ),
    )

    # ── wallets.last_classified_block ──────────────────────────────────────────
    op.add_column(
        "wallets",
        sa.Column("last_classified_block", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wallets", "last_classified_block")
    op.drop_table("l2_decoder_config")
    op.drop_index(
        "ix_classified_contract_address", table_name="classified_transactions"
    )
    op.drop_index(
        "ix_classified_wallet_block", table_name="classified_transactions"
    )
    op.drop_table("classified_transactions")
