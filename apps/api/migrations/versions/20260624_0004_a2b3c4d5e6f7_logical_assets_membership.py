"""Create logical_assets + token_asset_memberships; seed LEMX pool; add WLEMX membership.

Revision ID: a2b3c4d5e6f7
Revises: f6a1b2c3d4e5
Create Date: 2026-06-24 00:04:00.000000

Design note: WLEMX (Wrapped LEMX) and native LEMX are economically equivalent
(1:1 wrap/unwrap, non-taxable relocation). Mapping both into the LEMX logical
asset ensures they share a single cost-basis pool per Rev. Proc. 2024-28.
See docs/decisions/ADR-0002-wlemx-lemx-canonical-membership.md.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2b3c4d5e6f7"
down_revision: str | None = "f6a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WLEMX_ADDR = "0x84862e65ebf37af91a8b85283b58505de3352588"
_LEMX_ADDR = "0x0000000000000000000000000000000000000000"
_LC = "lemonchain"

_INSERT_LOGICAL_ASSET = sa.text("""
    INSERT INTO logical_assets (id, symbol, name, asset_kind, created_at)
    VALUES (gen_random_uuid(), :symbol, :name, :asset_kind, now())
    ON CONFLICT (symbol) DO NOTHING
""")

_INSERT_MEMBERSHIP = sa.text("""
    INSERT INTO token_asset_memberships (id, token_id, logical_asset_id, created_at)
    SELECT gen_random_uuid(), tr.id, la.id, now()
    FROM   token_registry  tr
    JOIN   logical_assets  la ON la.symbol = :la_symbol
    WHERE  tr.chain             = :chain
      AND  tr.contract_address  = :contract_address
    ON CONFLICT (token_id) DO NOTHING
""")

_CHECK_TOKEN = sa.text("""
    SELECT id FROM token_registry
    WHERE chain = :chain AND contract_address = :addr
""")

_CHECK_LOGICAL = sa.text("""
    SELECT id FROM logical_assets WHERE symbol = :symbol
""")

_DELETE_MEMBERSHIPS = sa.text("""
    DELETE FROM token_asset_memberships
    WHERE token_id IN (
        SELECT id FROM token_registry
        WHERE chain = :chain AND contract_address IN (:wlemx_addr, :lemx_addr)
    )
""")

_DELETE_LOGICAL = sa.text("""
    DELETE FROM logical_assets WHERE symbol = :symbol
""")


def upgrade() -> None:
    # ── schema ────────────────────────────────────────────────────────────────
    op.create_table(
        "logical_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("asset_kind", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", name="uq_logical_assets_symbol"),
        sa.CheckConstraint(
            "asset_kind IN ('fungible','nft','stablecoin')",
            name="ck_logical_assets_kind",
        ),
    )

    op.create_table(
        "token_asset_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_id", sa.Uuid(), nullable=False),
        sa.Column("logical_asset_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_id", name="uq_token_asset_membership_token"),
        sa.ForeignKeyConstraint(
            ["token_id"], ["token_registry.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["logical_asset_id"], ["logical_assets.id"], ondelete="RESTRICT"
        ),
    )

    # ── data: fail loudly if prerequisite rows are absent ────────────────────
    bind = op.get_bind()

    lemx_row = bind.execute(_CHECK_TOKEN.bindparams(chain=_LC, addr=_LEMX_ADDR)).fetchone()
    if lemx_row is None:
        raise RuntimeError(
            f"Migration a2b3c4d5e6f7: native LEMX row missing from token_registry "
            f"(chain={_LC!r}, contract_address={_LEMX_ADDR!r}). "
            "Run the tier-1 seed migration first."
        )

    wlemx_row = bind.execute(_CHECK_TOKEN.bindparams(chain=_LC, addr=_WLEMX_ADDR)).fetchone()
    if wlemx_row is None:
        raise RuntimeError(
            f"Migration a2b3c4d5e6f7: WLEMX row missing from token_registry "
            f"(chain={_LC!r}, contract_address={_WLEMX_ADDR!r}). "
            "Run the tier-1 seed migration first."
        )

    # Seed the LEMX logical asset.
    op.execute(
        _INSERT_LOGICAL_ASSET.bindparams(
            symbol="LEMX", name="Lemon (canonical pool)", asset_kind="fungible"
        )
    )

    # Map both native LEMX and WLEMX into the LEMX logical asset.
    op.execute(_INSERT_MEMBERSHIP.bindparams(la_symbol="LEMX", chain=_LC, contract_address=_LEMX_ADDR))
    op.execute(_INSERT_MEMBERSHIP.bindparams(la_symbol="LEMX", chain=_LC, contract_address=_WLEMX_ADDR))

    # Confirm both memberships were inserted.
    logical_row = bind.execute(_CHECK_LOGICAL.bindparams(symbol="LEMX")).fetchone()
    if logical_row is None:  # pragma: no cover
        raise RuntimeError("Migration a2b3c4d5e6f7: LEMX logical_asset insert failed.")


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        _DELETE_MEMBERSHIPS.bindparams(
            chain=_LC, wlemx_addr=_WLEMX_ADDR, lemx_addr=_LEMX_ADDR
        )
    )
    op.execute(_DELETE_LOGICAL.bindparams(symbol="LEMX"))
    op.drop_table("token_asset_memberships")
    op.drop_table("logical_assets")
