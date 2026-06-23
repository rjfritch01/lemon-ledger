"""name=historical_prices

Revision ID: a1b2c3d4e5f6
Revises: 0edc18d4c0a5
Create Date: 2026-06-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "0edc18d4c0a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "historical_prices",
        sa.Column("chain", sa.Text(), nullable=False),
        sa.Column("token_id", sa.Text(), nullable=False),
        sa.Column("day_timestamp", sa.BigInteger(), nullable=False),
        sa.Column("average_price_usd", sa.Numeric(38, 18), nullable=False),
        sa.Column("data_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "chain IN ('lemonchain','bsc')",
            name="ck_historical_prices_chain",
        ),
        sa.CheckConstraint(
            "source IN ('oracle','coingecko','manual')",
            name="ck_historical_prices_source",
        ),
        sa.PrimaryKeyConstraint("chain", "token_id", "day_timestamp"),
    )
    op.create_index(
        "ix_historical_prices_token_day",
        "historical_prices",
        ["chain", "token_id", "day_timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_historical_prices_token_day", table_name="historical_prices")
    op.drop_table("historical_prices")
