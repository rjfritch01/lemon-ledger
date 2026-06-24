"""Add deflationary/buy_burn_wallet to l2_decoder_config; create burn_addresses table.

Revision ID: e5f6a1b2c3d4
Revises: d4e5f6a1b2c3
Create Date: 2026-06-24 00:02:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5f6a1b2c3d4"
down_revision: str | None = "d4e5f6a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── l2_decoder_config: new columns ────────────────────────────────────────
    op.add_column(
        "l2_decoder_config",
        sa.Column("deflationary", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "l2_decoder_config",
        sa.Column("buy_burn_wallet", sa.Text(), nullable=True),
    )

    # ── burn_addresses table ───────────────────────────────────────────────────
    op.create_table(
        "burn_addresses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column(
            "token_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("token_registry.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("confidence", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "confidence IN ('universal','discovered','confirmed')",
            name="ck_burn_addr_confidence",
        ),
    )
    op.create_index("ix_burn_addresses_address", "burn_addresses", ["address"])


def downgrade() -> None:
    op.drop_index("ix_burn_addresses_address", table_name="burn_addresses")
    op.drop_table("burn_addresses")
    op.drop_column("l2_decoder_config", "buy_burn_wallet")
    op.drop_column("l2_decoder_config", "deflationary")
