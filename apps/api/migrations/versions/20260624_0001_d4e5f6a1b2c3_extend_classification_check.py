"""Extend ck_classification_kind to include 1.6 taxonomy additions.

New values: pending, wrap, unwrap, swap-credit-redemption, burn

Revision ID: d4e5f6a1b2c3
Revises: c3d4e5f6a1b2
Create Date: 2026-06-24 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a1b2c3"
down_revision: str | None = "c3d4e5f6a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_KINDS = (
    "'reward','mint','stake','unstake','transfer-in','transfer-out','unclassified'"
)
_NEW_KINDS = (
    "'reward','mint','stake','unstake','transfer-in','transfer-out','unclassified',"
    "'pending','wrap','unwrap','swap-credit-redemption','burn'"
)


def upgrade() -> None:
    op.drop_constraint("ck_classification_kind", "classified_transactions", type_="check")
    op.create_check_constraint(
        "ck_classification_kind",
        "classified_transactions",
        f"classification IN ({_NEW_KINDS})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_classification_kind", "classified_transactions", type_="check")
    op.create_check_constraint(
        "ck_classification_kind",
        "classified_transactions",
        f"classification IN ({_OLD_KINDS})",
    )
