"""1.9 Stage 4: extend lot_relocations.reason CHECK to add 'internal' and 'reassignment'.

Needed for cross-entity relocate-internal and relocate-reassignment paths introduced
in Stage 4.  The existing values ('wrap','unwrap','bridge','cap-contribution','gift','loan')
are preserved.

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-06-25 18:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "c0d1e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_VALS = "'wrap','unwrap','bridge','cap-contribution','gift','loan'"
_NEW_VALS = "'wrap','unwrap','bridge','cap-contribution','gift','loan','internal','reassignment'"
_CONSTRAINT = "ck_lot_relocations_reason"
_TABLE = "lot_relocations"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, f"reason IN ({_NEW_VALS})")


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, f"reason IN ({_OLD_VALS})")
