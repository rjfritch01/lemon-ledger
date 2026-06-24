"""Seed l2_decoder_config rows for the 10 cross-chain L2 tokens (Lemonchain side).

Addresses left 'unknown'; mint_fee_wei NULL (fee is USD-denominated, priced in
LEMX, so a fixed wei amount would be wrong).

Revision ID: c3d4e5f6a1b2
Revises: b2c3d4e5f6a1
Create Date: 2026-06-23 00:02:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a1b2"
down_revision: str | None = "b2c3d4e5f6a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEMONCHAIN = "lemonchain"

_DECODERS: tuple[tuple[str, str], ...] = (
    ("LFLX", "LflxDecoder"),
    ("LBNK", "LbnkDecoder"),
    ("LPAY", "LpayDecoder"),
    ("LMED", "LmedDecoder"),
    ("CTFZ", "CtfzDecoder"),
    ("LLOT", "LlotDecoder"),
    ("LLUX", "LluxDecoder"),
    ("LMLN", "LmlnDecoder"),
    ("LSQZ", "LsqzDecoder"),
    ("LTVL", "LtvlDecoder"),
)

_INSERT = sa.text("""
    INSERT INTO l2_decoder_config
        (id, token_id, chain, decoder_class,
         mint_fee_wei, burn_and_mint,
         nft_contract_status, staking_contract_status, distribution_complete,
         created_at, updated_at)
    SELECT gen_random_uuid(), tr.id, :chain, :cls,
           NULL, false,
           'unknown', 'unknown', false, now(), now()
    FROM token_registry tr
    WHERE tr.chain = :chain AND tr.symbol = :sym
    ON CONFLICT (token_id) DO NOTHING
""")

_DELETE = sa.text("""
    DELETE FROM l2_decoder_config
    WHERE chain = :chain
      AND decoder_class IN (
          'LflxDecoder','LbnkDecoder','LpayDecoder','LmedDecoder','CtfzDecoder',
          'LlotDecoder','LluxDecoder','LmlnDecoder','LsqzDecoder','LtvlDecoder'
      )
""")


def upgrade() -> None:
    for sym, cls in _DECODERS:
        op.execute(_INSERT.bindparams(chain=_LEMONCHAIN, cls=cls, sym=sym))


def downgrade() -> None:
    op.execute(_DELETE.bindparams(chain=_LEMONCHAIN))
