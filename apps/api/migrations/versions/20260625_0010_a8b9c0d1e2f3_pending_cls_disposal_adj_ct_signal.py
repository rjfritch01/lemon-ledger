"""1.9 schema: pending_classifications, lot_disposals 8949 columns, CT transfer_resolution.

Revision ID: a8b9c0d1e2f3
Revises: f7a2b3c4d5e6
Create Date: 2026-06-25 00:10:00.000000

Changes:
  1. lot_disposals: add covered_status (NOT NULL default 'no-1099-da'), adjustment_code (nullable),
     adjustment_usd (nullable NUMERIC) — Form 8949 columns (box / f / g).
  2. Create pending_classifications table: staging area for cross-entity and external-outflow
     transfers that require user or rule resolution before lot math can proceed.
  3. classified_transactions: add transfer_resolution (nullable text + CHECK) — engine-signal
     column stamped by the resolve service; the lot engine reads only this field and
     relocation_source_event_id, never pending_classifications or bridge_correlations.
  4. Recreate v_lot_gate: keep all 1.8 bridge sources; add BLOCKING source for
     pending_classifications WHERE state='needs_classification'.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: str | None = "f7a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRANSFER_RES_VALUES = (
    "'relocate-internal','relocate-contribution','relocate-gift','relocate-reassignment',"
    "'disposal','disposal-related-party','gift-out','no-op-loan'"
)
_COVERED_STATUS_VALUES = "'no-1099-da','covered-basis-reported','covered-basis-not-reported'"
_ADJ_CODE_VALUES = "'L','W','D','E','O'"
_PENDING_KIND_VALUES = "'cross-entity','external-outflow'"
_PENDING_STATE_VALUES = "'needs_classification','classified','applied','dismissed'"
_CHOSEN_CLS_VALUES = (
    "'capital-contribution','sale','gift','loan','reassignment','payment'"
)
_RESOLVED_BY_VALUES = "'user','rule'"

# ── v_lot_gate versions ───────────────────────────────────────────────────────

# 1.8 text (for downgrade target).
_V_LOT_GATE_1_8 = """
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
"""

# 1.9 text adds the pending_classifications blocking source.
_V_LOT_GATE_1_9 = """
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
           w.user_id AS wallet_id,
           'bridge:needs_confirmation' AS reason,
           ct_out.occurred_at, true AS blocking
    FROM bridge_correlations bc
    JOIN classified_transactions ct_out ON ct_out.id = bc.outflow_classified_event_id
    JOIN wallets w ON w.id = ct_out.wallet_id
    WHERE bc.status = 'needs_confirmation'

    UNION ALL

    -- (d) Aged-out unmatched bridge legs (non-blocking notice)
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

_DROP_VIEW = "DROP VIEW IF EXISTS v_lot_gate"


def upgrade() -> None:
    # 1. lot_disposals: add Form 8949 columns.
    op.add_column(
        "lot_disposals",
        sa.Column(
            "covered_status",
            sa.Text(),
            nullable=False,
            server_default="no-1099-da",
        ),
    )
    op.add_column("lot_disposals", sa.Column("adjustment_code", sa.Text(), nullable=True))
    op.add_column(
        "lot_disposals",
        sa.Column("adjustment_usd", sa.Numeric(38, 18), nullable=True),
    )
    op.create_check_constraint(
        "ck_lot_disposals_covered_status",
        "lot_disposals",
        f"covered_status IN ({_COVERED_STATUS_VALUES})",
    )
    op.create_check_constraint(
        "ck_lot_disposals_adjustment_code",
        "lot_disposals",
        f"adjustment_code IS NULL OR adjustment_code IN ({_ADJ_CODE_VALUES})",
    )

    # 2. pending_classifications table.
    op.create_table(
        "pending_classifications",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("logical_transfer_key", sa.Text(), nullable=False),
        sa.Column("chain", sa.Text(), nullable=False),
        sa.Column("tx_hash", sa.Text(), nullable=False),
        sa.Column("transfer_index", sa.Integer(), nullable=False),
        sa.Column("token_id", sa.UUID(as_uuid=True), sa.ForeignKey("token_registry.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("canonical_asset", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(78, 18), nullable=False),
        sa.Column("from_wallet_id", sa.UUID(as_uuid=True), sa.ForeignKey("wallets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("from_entity_id", sa.UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("to_wallet_id", sa.UUID(as_uuid=True), sa.ForeignKey("wallets.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("to_entity_id", sa.UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("to_address", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False, server_default="needs_classification"),
        sa.Column("chosen_classification", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.Text(), nullable=True),
        sa.Column("resolved_rule_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("dismiss_reason", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("logical_transfer_key", name="uq_pending_cls_transfer_key"),
        sa.CheckConstraint(f"kind IN ({_PENDING_KIND_VALUES})", name="ck_pending_cls_kind"),
        sa.CheckConstraint(f"state IN ({_PENDING_STATE_VALUES})", name="ck_pending_cls_state"),
        sa.CheckConstraint(
            f"chosen_classification IS NULL OR chosen_classification IN ({_CHOSEN_CLS_VALUES})",
            name="ck_pending_cls_chosen",
        ),
        sa.CheckConstraint(
            f"resolved_by IS NULL OR resolved_by IN ({_RESOLVED_BY_VALUES})",
            name="ck_pending_cls_resolved_by",
        ),
    )
    op.create_index("ix_pending_cls_user_state", "pending_classifications", ["user_id", "state"])

    # 3. classified_transactions: add transfer_resolution signal column.
    op.add_column(
        "classified_transactions",
        sa.Column("transfer_resolution", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_classified_transfer_resolution",
        "classified_transactions",
        f"transfer_resolution IS NULL OR transfer_resolution IN ({_TRANSFER_RES_VALUES})",
    )

    # 4. Recreate v_lot_gate with pending_classifications blocking source.
    op.execute(sa.text(_DROP_VIEW))
    op.execute(sa.text(_V_LOT_GATE_1_9))


def downgrade() -> None:
    # 4. Restore 1.8 v_lot_gate (drops pending_classifications source).
    op.execute(sa.text(_DROP_VIEW))
    op.execute(sa.text(_V_LOT_GATE_1_8))

    # 3. Drop transfer_resolution from classified_transactions.
    op.drop_constraint(
        "ck_classified_transfer_resolution", "classified_transactions", type_="check"
    )
    op.drop_column("classified_transactions", "transfer_resolution")

    # 2. Drop pending_classifications.
    op.drop_index("ix_pending_cls_user_state", table_name="pending_classifications")
    op.drop_table("pending_classifications")

    # 1. Drop lot_disposals 8949 columns.
    op.drop_constraint("ck_lot_disposals_adjustment_code", "lot_disposals", type_="check")
    op.drop_constraint("ck_lot_disposals_covered_status", "lot_disposals", type_="check")
    op.drop_column("lot_disposals", "adjustment_usd")
    op.drop_column("lot_disposals", "adjustment_code")
    op.drop_column("lot_disposals", "covered_status")
