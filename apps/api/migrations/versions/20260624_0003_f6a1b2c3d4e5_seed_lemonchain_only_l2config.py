"""Seed l2_decoder_config for 9 Lemonchain-only tokens + LBST (parked, BSC) + LQST.

Also:
  - Inserts token_registry rows for LQST, SCDT (LC), LBST, SCDT (BSC) if absent.
  - Sets deflationary=true on the 5 deflationary Lemonchain tokens (LMLN, LPAY,
    LFLX, LLOT, LMED) via UPDATE on their existing l2_decoder_config rows.
  - Seeds universal burn sinks (0x0, 0xdead) in burn_addresses.

Revision ID: f6a1b2c3d4e5
Revises: e5f6a1b2c3d4
Create Date: 2026-06-24 00:03:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a1b2c3d4e5"
down_revision: str | None = "e5f6a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LC = "lemonchain"
_BSC = "bsc"
_ZERO = "0x0000000000000000000000000000000000000000"
_DEAD = "0x000000000000000000000000000000000000dead"

# ── 9 Lemonchain-only thin stubs (all previously cross-chain only) ─────────────
_LC_ONLY_DECODERS: tuple[tuple[str, str], ...] = (
    ("HXDX", "HxdxDecoder"),
    ("HXBT", "HxbtDecoder"),
    ("MHSA", "MhsaDecoder"),
    ("NXYS", "NxysDecoder"),
    ("PUP", "PupDecoder"),
    ("RMC", "RmcDecoder"),
    ("SMART", "SmartDecoder"),
    ("STH", "SthDecoder"),
    ("TIXA", "TixaDecoder"),
)

# ── token_registry rows for NFT / BSC-only tokens not yet seeded ───────────────
# chain, symbol, name, contract_address, decimals, category, is_deflationary,
# max_supply, pricing_source_primary, pricing_source_fallback, project_metadata
_NEW_TOKENS: tuple[tuple, ...] = (
    (
        _LC,
        "LQST",
        "LemQuest",
        "0x3e2f7f34ec743a78d4682de27890e4dc2ba543a4",
        0,
        "ecosystem-l2",
        False,
        None,
        None,
        None,
        '{}',
    ),
    (
        _LC,
        "SCDT",
        "SwapCredit",
        "0xe20e3c6447b024a3fbf22d4803de1d910add7776",
        0,
        "ecosystem-l2",
        False,
        None,
        None,
        None,
        '{}',
    ),
    (
        _BSC,
        "LBST",
        "LemBoost",
        "0x1682c43e6ca82b6f2c3269670e9a9d3c182e4add",
        18,
        "ecosystem-l2",
        False,
        None,
        None,
        None,
        '{}',
    ),
    (
        _BSC,
        "SCDT",
        "SwapCredit",
        "0xef50c3b7a6de0d006779400e958aca4360f14e5a",
        0,
        "ecosystem-l2",
        False,
        None,
        None,
        None,
        '{}',
    ),
)

_INSERT_TOKEN = sa.text("""
    INSERT INTO token_registry
        (id, chain, symbol, name, contract_address, decimals, category,
         is_deflationary, max_supply, pricing_source_primary,
         pricing_source_fallback, project_metadata, tier,
         created_at, updated_at)
    VALUES (
        gen_random_uuid(), :chain, :symbol, :name, :addr, :dec, :cat,
        :deflationary, :max_supply, :src_primary, :src_fallback,
        CAST(:project_metadata AS JSONB), 2,
        now(), now()
    )
    ON CONFLICT (chain, contract_address) DO NOTHING
""")

_INSERT_CFG = sa.text("""
    INSERT INTO l2_decoder_config
        (id, token_id, chain, decoder_class,
         mint_fee_wei, burn_and_mint,
         nft_contract_status, staking_contract_status, distribution_complete,
         deflationary, created_at, updated_at)
    SELECT gen_random_uuid(), tr.id, :chain, :cls,
           NULL, false,
           'unknown', 'unknown', false,
           false, now(), now()
    FROM token_registry tr
    WHERE tr.chain = :chain AND tr.symbol = :sym
    ON CONFLICT (token_id) DO NOTHING
""")

# LQST: nft_contract seeded from known address; nft_contract_status='confirmed'
_INSERT_LQST_CFG = sa.text("""
    INSERT INTO l2_decoder_config
        (id, token_id, chain, decoder_class,
         nft_contract, nft_contract_status,
         mint_fee_wei, burn_and_mint,
         staking_contract_status, distribution_complete,
         deflationary, created_at, updated_at)
    SELECT gen_random_uuid(), tr.id, :chain, 'LqstDecoder',
           :nft_contract, 'confirmed',
           NULL, false,
           'unknown', false,
           false, now(), now()
    FROM token_registry tr
    WHERE tr.chain = :chain AND tr.symbol = 'LQST'
    ON CONFLICT (token_id) DO NOTHING
""")

_UPDATE_DEFLATIONARY = sa.text("""
    UPDATE l2_decoder_config
    SET deflationary = true
    WHERE chain = 'lemonchain'
      AND decoder_class IN (
          'LmlnDecoder', 'LpayDecoder', 'LflxDecoder', 'LlotDecoder', 'LmedDecoder'
      )
""")

_INSERT_BURN_SINK = sa.text("""
    INSERT INTO burn_addresses (id, address, token_id, confidence, notes, created_at)
    VALUES (gen_random_uuid(), :addr, NULL, 'universal', :notes, now())
    ON CONFLICT DO NOTHING
""")

_DELETE_CFG = sa.text("""
    DELETE FROM l2_decoder_config
    WHERE chain = :chain
      AND decoder_class IN (
          'HxdxDecoder','HxbtDecoder','MhsaDecoder','NxysDecoder','PupDecoder',
          'RmcDecoder','SmartDecoder','SthDecoder','TixaDecoder',
          'LqstDecoder','LbstDecoder'
      )
""")

_DELETE_NEW_TOKENS = sa.text("""
    DELETE FROM token_registry
    WHERE (chain = 'lemonchain' AND symbol IN ('LQST','SCDT'))
       OR (chain = 'bsc'        AND symbol IN ('LBST','SCDT'))
""")

_REVERT_DEFLATIONARY = sa.text("""
    UPDATE l2_decoder_config
    SET deflationary = false
    WHERE chain = 'lemonchain'
      AND decoder_class IN (
          'LmlnDecoder', 'LpayDecoder', 'LflxDecoder', 'LlotDecoder', 'LmedDecoder'
      )
""")


def upgrade() -> None:
    # ── token_registry: add NFT / BSC-only entries ────────────────────────────
    for row in _NEW_TOKENS:
        (chain, symbol, name, addr, dec, cat,
         deflationary, max_supply, src_primary, src_fallback, meta) = row
        op.execute(
            _INSERT_TOKEN.bindparams(
                chain=chain, symbol=symbol, name=name, addr=addr,
                dec=dec, cat=cat, deflationary=deflationary,
                max_supply=max_supply, src_primary=src_primary,
                src_fallback=src_fallback, project_metadata=meta,
            )
        )

    # ── l2_decoder_config: 9 Lemonchain-only stubs ───────────────────────────
    for sym, cls in _LC_ONLY_DECODERS:
        op.execute(_INSERT_CFG.bindparams(chain=_LC, cls=cls, sym=sym))

    # ── l2_decoder_config: LQST (mint-only, nft_contract known) ─────────────
    op.execute(
        _INSERT_LQST_CFG.bindparams(
            chain=_LC,
            nft_contract="0x3e2f7f34ec743a78d4682de27890e4dc2ba543a4",
        )
    )

    # ── l2_decoder_config: LBST (BSC-only, parked stub) ─────────────────────
    op.execute(_INSERT_CFG.bindparams(chain=_BSC, cls="LbstDecoder", sym="LBST"))

    # ── mark deflationary tokens ─────────────────────────────────────────────
    op.execute(_UPDATE_DEFLATIONARY)

    # ── universal burn sinks ─────────────────────────────────────────────────
    op.execute(
        _INSERT_BURN_SINK.bindparams(
            addr=_ZERO, notes="EVM zero address — universal burn sink"
        )
    )
    op.execute(
        _INSERT_BURN_SINK.bindparams(
            addr=_DEAD, notes="0xdead — conventional EVM burn address"
        )
    )


def downgrade() -> None:
    # Delete l2_decoder_config rows before token_registry rows (FK constraint)
    op.execute(_DELETE_CFG.bindparams(chain=_LC))
    op.execute(_DELETE_CFG.bindparams(chain=_BSC))
    op.execute(sa.text("DELETE FROM burn_addresses WHERE confidence = 'universal'"))
    op.execute(_REVERT_DEFLATIONARY)
    op.execute(_DELETE_NEW_TOKENS)
