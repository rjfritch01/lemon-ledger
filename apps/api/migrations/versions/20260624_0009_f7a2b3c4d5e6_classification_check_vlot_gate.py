"""Extend classification CHECK for bridge-in/bridge-out; recreate v_lot_gate with bridge sources.

Revision ID: f7a2b3c4d5e6
Revises: e6f7a2b3c4d5
Create Date: 2026-06-24 00:09:00.000000

Changes:
  1. Extends ck_classification_kind to include 'bridge-in' and 'bridge-out'.
  2. Drops the 1.7 v_lot_gate view and recreates it with bridge correlation sources:
     - BLOCKING: CTs in 'pending' / needs_review = true
     - BLOCKING: unresolved lot processing exceptions
     - BLOCKING: bridge pairs in 'needs_confirmation' status
     - NON-BLOCKING notice: bridge pairs in 'unmatched' status where the leg
       has been aged-out and stamped to the taxable fallback (needs_review=true
       on the CT catches these; kept here as explicit bridge source too).
  3. Seeds Phase-1 curated custody addresses from the LEMX bridge fixture data.
     TODO: replace the placeholder address below once real captures are confirmed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7a2b3c4d5e6"
down_revision: str | None = "e6f7a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_KINDS = (
    "'reward','mint','stake','unstake','transfer-in','transfer-out','unclassified',"
    "'pending','wrap','unwrap','swap-credit-redemption','burn'"
)
_NEW_KINDS = (
    "'reward','mint','stake','unstake','transfer-in','transfer-out','unclassified',"
    "'pending','wrap','unwrap','swap-credit-redemption','burn','bridge-in','bridge-out'"
)

# 1.7 v_lot_gate text (for downgrade).
_V_LOT_GATE_1_7 = """
CREATE OR REPLACE VIEW v_lot_gate AS
    -- (a) Classified events that are pending or need review
    SELECT
        ct.id           AS classified_tx_id,
        ct.wallet_id,
        ct.classification AS reason,
        ct.occurred_at
    FROM classified_transactions ct
    WHERE ct.classification = 'pending'
       OR ct.needs_review = true

    UNION ALL

    -- (b) Lot processing exceptions not yet resolved
    SELECT
        exc.classified_tx_id,
        ct.wallet_id,
        exc.reason,
        ct.occurred_at
    FROM lot_processing_exceptions exc
    JOIN classified_transactions ct ON ct.id = exc.classified_tx_id
    WHERE exc.resolved_at IS NULL
"""

# 1.8 v_lot_gate adds blocking boolean + bridge sources.
_V_LOT_GATE_1_8 = """
CREATE OR REPLACE VIEW v_lot_gate AS
    -- (a) Classified events pending or flagged for review (blocking)
    SELECT
        ct.id           AS classified_tx_id,
        ct.wallet_id,
        ct.classification AS reason,
        ct.occurred_at,
        true            AS blocking
    FROM classified_transactions ct
    WHERE ct.classification = 'pending'
       OR ct.needs_review = true

    UNION ALL

    -- (b) Lot processing exceptions not yet resolved (blocking)
    SELECT
        exc.classified_tx_id,
        ct.wallet_id,
        exc.reason,
        ct.occurred_at,
        true AS blocking
    FROM lot_processing_exceptions exc
    JOIN classified_transactions ct ON ct.id = exc.classified_tx_id
    WHERE exc.resolved_at IS NULL

    UNION ALL

    -- (c) Bridge pairs awaiting user confirmation (blocking)
    SELECT
        bc.outflow_classified_event_id AS classified_tx_id,
        w.user_id                      AS wallet_id,
        'bridge:needs_confirmation'    AS reason,
        ct_out.occurred_at,
        true                           AS blocking
    FROM bridge_correlations bc
    JOIN classified_transactions ct_out
        ON ct_out.id = bc.outflow_classified_event_id
    JOIN wallets w ON w.id = ct_out.wallet_id
    WHERE bc.status = 'needs_confirmation'

    UNION ALL

    -- (d) Aged-out unmatched bridge legs surfaced for reclassification (non-blocking)
    SELECT
        COALESCE(bc.outflow_classified_event_id, bc.inflow_classified_event_id)
            AS classified_tx_id,
        w.user_id AS wallet_id,
        'bridge:aged_unmatched' AS reason,
        COALESCE(ct_out.occurred_at, ct_in.occurred_at) AS occurred_at,
        false AS blocking
    FROM bridge_correlations bc
    LEFT JOIN classified_transactions ct_out
        ON ct_out.id = bc.outflow_classified_event_id
    LEFT JOIN classified_transactions ct_in
        ON ct_in.id = bc.inflow_classified_event_id
    JOIN wallets w
        ON w.id = COALESCE(ct_out.wallet_id, ct_in.wallet_id)
    WHERE bc.status = 'unmatched'
      AND bc.user_resolution IS NULL
"""

_DROP_VIEW = "DROP VIEW IF EXISTS v_lot_gate"

# Phase-1 curated custody address seed.
# TODO: replace placeholder once real Lemonchain bridge contract address is confirmed
# from sponsor wallet captures. Set LEMX_BRIDGE_CONTRACT to the real value.
_CUSTODY_SEED: list[dict[str, str]] = [
    # Placeholder — skipped if address is the zero address sentinel.
    # {
    #     "chain": "lemonchain",
    #     "address": "0x<real_bridge_contract>",
    #     "recognition": "recognized",
    #     "source": "curated",
    #     "note": "Lemonchain native bridge lock contract (Phase-1 sponsor capture)",
    # },
]


def upgrade() -> None:
    # 1. Extend classification CHECK.
    op.drop_constraint("ck_classification_kind", "classified_transactions", type_="check")
    op.create_check_constraint(
        "ck_classification_kind",
        "classified_transactions",
        f"classification IN ({_NEW_KINDS})",
    )

    # 2. Recreate v_lot_gate with bridge sources + blocking column.
    op.execute(sa.text(_DROP_VIEW))
    op.execute(sa.text(_V_LOT_GATE_1_8))

    # 3. Seed curated custody addresses.
    if _CUSTODY_SEED:
        bind = op.get_bind()
        for row in _CUSTODY_SEED:
            bind.execute(
                sa.text("""
                    INSERT INTO custody_addresses
                        (id, chain, address, recognition, source, note, created_at, updated_at)
                    VALUES
                        (gen_random_uuid(), :chain, :address, :recognition, :source, :note,
                         now(), now())
                    ON CONFLICT (chain, address) DO NOTHING
                """),
                row,
            )


def downgrade() -> None:
    # Remove seeded rows (curated only; learned rows not touched).
    if _CUSTODY_SEED:
        bind = op.get_bind()
        for row in _CUSTODY_SEED:
            bind.execute(
                sa.text(
                    "DELETE FROM custody_addresses WHERE chain = :chain AND address = :address"
                    " AND source = 'curated'"
                ),
                {"chain": row["chain"], "address": row["address"]},
            )

    # Restore 1.7 v_lot_gate (drops blocking column).
    op.execute(sa.text(_DROP_VIEW))
    op.execute(sa.text(_V_LOT_GATE_1_7))

    # Restore classification CHECK.
    op.drop_constraint("ck_classification_kind", "classified_transactions", type_="check")
    op.create_check_constraint(
        "ck_classification_kind",
        "classified_transactions",
        f"classification IN ({_OLD_KINDS})",
    )
