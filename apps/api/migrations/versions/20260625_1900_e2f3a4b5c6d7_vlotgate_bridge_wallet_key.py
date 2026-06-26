"""Fix v_lot_gate sources (c) and (d): emit wallet_id not user_id.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-06-25 19:00:00.000000

Bug: sources (c) and (d) emitted w.user_id AS wallet_id (a user UUID) instead of
the wallet UUID.  Any consumer filtering v_lot_gate by wallet_id would silently miss
bridge:needs_confirmation and bridge:aged_unmatched rows.

Fix: drop the JOIN wallets w from both sources; emit ct_out.wallet_id (c) and
COALESCE(ct_out.wallet_id, ct_in.wallet_id) (d) — matching the pattern of
sources (a), (b), and (e).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: str | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DROP_VIEW = "DROP VIEW IF EXISTS v_lot_gate"

# Buggy version (for downgrade).
_V_LOT_GATE_BUGGY = """
CREATE OR REPLACE VIEW v_lot_gate AS
    SELECT ct.id AS classified_tx_id, ct.wallet_id, ct.classification AS reason,
           ct.occurred_at, true AS blocking
    FROM classified_transactions ct
    WHERE ct.classification = 'pending' OR ct.needs_review = true

    UNION ALL

    SELECT exc.classified_tx_id, ct.wallet_id, exc.reason, ct.occurred_at, true AS blocking
    FROM lot_processing_exceptions exc
    JOIN classified_transactions ct ON ct.id = exc.classified_tx_id
    WHERE exc.resolved_at IS NULL

    UNION ALL

    SELECT bc.outflow_classified_event_id AS classified_tx_id,
           w.user_id AS wallet_id,
           'bridge:needs_confirmation' AS reason,
           ct_out.occurred_at, true AS blocking
    FROM bridge_correlations bc
    JOIN classified_transactions ct_out ON ct_out.id = bc.outflow_classified_event_id
    JOIN wallets w ON w.id = ct_out.wallet_id
    WHERE bc.status = 'needs_confirmation'

    UNION ALL

    SELECT COALESCE(bc.outflow_classified_event_id, bc.inflow_classified_event_id)
               AS classified_tx_id,
           w.user_id AS wallet_id,
           'bridge:aged_unmatched' AS reason,
           COALESCE(ct_out.occurred_at, ct_in.occurred_at) AS occurred_at,
           false AS blocking
    FROM bridge_correlations bc
    LEFT JOIN classified_transactions ct_out ON ct_out.id = bc.outflow_classified_event_id
    LEFT JOIN classified_transactions ct_in  ON ct_in.id  = bc.inflow_classified_event_id
    JOIN wallets w ON w.id = COALESCE(ct_out.wallet_id, ct_in.wallet_id)
    WHERE bc.status = 'unmatched' AND bc.user_resolution IS NULL

    UNION ALL

    SELECT ct.id AS classified_tx_id,
           ct.wallet_id,
           'cross-entity:needs_classification' AS reason,
           pc.detected_at AS occurred_at,
           true AS blocking
    FROM pending_classifications pc
    JOIN classified_transactions ct
        ON  ct.wallet_id  = pc.from_wallet_id
        AND ct.tx_hash    = pc.tx_hash
        AND ct.event_seq  = pc.transfer_index
    WHERE pc.state = 'needs_classification'
"""

# Fixed version: sources (c) and (d) use wallet UUIDs, not user UUIDs.
_V_LOT_GATE_FIXED = """
CREATE OR REPLACE VIEW v_lot_gate AS
    -- (a) CTs pending or flagged for review (blocking)
    SELECT ct.id AS classified_tx_id, ct.wallet_id, ct.classification AS reason,
           ct.occurred_at, true AS blocking
    FROM classified_transactions ct
    WHERE ct.classification = 'pending' OR ct.needs_review = true

    UNION ALL

    -- (b) Unresolved lot processing exceptions (blocking)
    SELECT exc.classified_tx_id, ct.wallet_id, exc.reason, ct.occurred_at, true AS blocking
    FROM lot_processing_exceptions exc
    JOIN classified_transactions ct ON ct.id = exc.classified_tx_id
    WHERE exc.resolved_at IS NULL

    UNION ALL

    -- (c) Bridge pairs awaiting user confirmation (blocking)
    SELECT bc.outflow_classified_event_id AS classified_tx_id,
           ct_out.wallet_id,
           'bridge:needs_confirmation' AS reason,
           ct_out.occurred_at, true AS blocking
    FROM bridge_correlations bc
    JOIN classified_transactions ct_out ON ct_out.id = bc.outflow_classified_event_id
    WHERE bc.status = 'needs_confirmation'

    UNION ALL

    -- (d) Aged-out unmatched bridge legs (non-blocking notice)
    SELECT COALESCE(bc.outflow_classified_event_id, bc.inflow_classified_event_id)
               AS classified_tx_id,
           COALESCE(ct_out.wallet_id, ct_in.wallet_id),
           'bridge:aged_unmatched' AS reason,
           COALESCE(ct_out.occurred_at, ct_in.occurred_at) AS occurred_at,
           false AS blocking
    FROM bridge_correlations bc
    LEFT JOIN classified_transactions ct_out ON ct_out.id = bc.outflow_classified_event_id
    LEFT JOIN classified_transactions ct_in  ON ct_in.id  = bc.inflow_classified_event_id
    WHERE bc.status = 'unmatched' AND bc.user_resolution IS NULL

    UNION ALL

    -- (e) Cross-entity / external-outflow transfers awaiting classification (blocking)
    SELECT ct.id AS classified_tx_id,
           ct.wallet_id,
           'cross-entity:needs_classification' AS reason,
           pc.detected_at AS occurred_at,
           true AS blocking
    FROM pending_classifications pc
    JOIN classified_transactions ct
        ON  ct.wallet_id  = pc.from_wallet_id
        AND ct.tx_hash    = pc.tx_hash
        AND ct.event_seq  = pc.transfer_index
    WHERE pc.state = 'needs_classification'
"""


def upgrade() -> None:
    op.execute(_DROP_VIEW)
    op.execute(_V_LOT_GATE_FIXED)


def downgrade() -> None:
    op.execute(_DROP_VIEW)
    op.execute(_V_LOT_GATE_BUGGY)
