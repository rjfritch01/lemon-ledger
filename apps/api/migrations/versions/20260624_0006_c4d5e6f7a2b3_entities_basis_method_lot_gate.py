"""Alter entities.default_basis_method CHECK; create v_lot_gate view.

Revision ID: c4d5e6f7a2b3
Revises: b3c4d5e6f7a2
Create Date: 2026-06-24 00:06:00.000000

Changes:
  1. Drops the old ck_entities_default_basis_method CHECK (allows 'fifo','hifo','specific-id')
     and replaces it with one that only allows ('fifo','specific_id').
     Average Cost is intentionally absent (not permitted for US crypto holdings).
     Fails loudly if any existing row has a disallowed value.
  2. Creates read-only VIEW v_lot_gate surfacing pending/exception lot rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d5e6f7a2b3"
down_revision: str | None = "b3c4d5e6f7a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_ALLOWED = ("fifo", "specific_id")
_OLD_ALLOWED = ("fifo", "hifo", "specific-id")

_V_LOT_GATE_SQL = """
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

_DROP_V_LOT_GATE = "DROP VIEW IF EXISTS v_lot_gate"


def upgrade() -> None:
    bind = op.get_bind()

    # Guard: fail loudly if any row holds a value not in the new allowed set.
    placeholders = ", ".join(f"'{v}'" for v in _NEW_ALLOWED)
    bad = bind.execute(
        sa.text(
            f"SELECT id, default_basis_method FROM entities "  # noqa: S608
            f"WHERE default_basis_method NOT IN ({placeholders})"
        )
    ).fetchall()
    if bad:
        bad_repr = [(str(row[0]), row[1]) for row in bad]
        raise RuntimeError(
            f"Migration c4d5e6f7a2b3: cannot tighten default_basis_method CHECK — "
            f"{len(bad)} row(s) hold disallowed values: {bad_repr}. "
            "Migrate or remove those rows before running this migration."
        )

    # Drop old constraint and recreate with the restricted set.
    op.drop_constraint("ck_entities_default_basis_method", "entities", type_="check")
    op.create_check_constraint(
        "ck_entities_default_basis_method",
        "entities",
        f"default_basis_method IN ({placeholders})",
    )

    # Create the v_lot_gate view.
    op.execute(sa.text(_V_LOT_GATE_SQL))


def downgrade() -> None:
    op.execute(sa.text(_DROP_V_LOT_GATE))

    # Restore the old CHECK constraint.
    op.drop_constraint("ck_entities_default_basis_method", "entities", type_="check")
    old_placeholders = ", ".join(f"'{v}'" for v in _OLD_ALLOWED)
    op.create_check_constraint(
        "ck_entities_default_basis_method",
        "entities",
        f"default_basis_method IN ({old_placeholders})",
    )
