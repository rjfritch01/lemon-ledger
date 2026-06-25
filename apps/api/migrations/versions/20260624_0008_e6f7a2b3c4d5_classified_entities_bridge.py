"""Add bridge FKs to classified_transactions; add jurisdiction/bridge_treatment to entities.

Revision ID: e6f7a2b3c4d5
Revises: d5e6f7a2b3c4
Create Date: 2026-06-24 00:08:00.000000

Changes:
  1. classified_transactions: add FK constraint on bridge_correlation_id (column already
     exists from initial schema); add relocation_source_event_id column + FK (self-ref).
  2. entities: add jurisdiction NOT NULL default 'US'; add bridge_treatment NOT NULL
     default 'relocate' + CHECK.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e6f7a2b3c4d5"
down_revision: str | None = "d5e6f7a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── classified_transactions ───────────────────────────────────────────────

    # FK for bridge_correlation_id (column already in DB; use_alter resolves circular ref).
    op.create_foreign_key(
        "fk_classified_bridge_correlation",
        "classified_transactions",
        "bridge_correlations",
        ["bridge_correlation_id"],
        ["id"],
        ondelete="RESTRICT",
        use_alter=True,
    )

    # New column: relocation_source_event_id (self-referential FK into classified_transactions).
    op.add_column(
        "classified_transactions",
        sa.Column(
            "relocation_source_event_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_classified_relocation_source",
        "classified_transactions",
        "classified_transactions",
        ["relocation_source_event_id"],
        ["id"],
        ondelete="RESTRICT",
        use_alter=True,
    )
    op.create_index(
        "ix_classified_relocation_source",
        "classified_transactions",
        ["relocation_source_event_id"],
    )

    # ── entities ──────────────────────────────────────────────────────────────

    op.add_column(
        "entities",
        sa.Column("jurisdiction", sa.Text(), nullable=False, server_default="US"),
    )
    op.add_column(
        "entities",
        sa.Column("bridge_treatment", sa.Text(), nullable=False, server_default="relocate"),
    )
    op.create_check_constraint(
        "ck_entities_bridge_treatment",
        "entities",
        "bridge_treatment IN ('relocate','disposition')",
    )


def downgrade() -> None:
    # entities
    op.drop_constraint("ck_entities_bridge_treatment", "entities", type_="check")
    op.drop_column("entities", "bridge_treatment")
    op.drop_column("entities", "jurisdiction")

    # classified_transactions
    op.drop_index("ix_classified_relocation_source", table_name="classified_transactions")
    op.drop_constraint(
        "fk_classified_relocation_source", "classified_transactions", type_="foreignkey"
    )
    op.drop_column("classified_transactions", "relocation_source_event_id")
    op.drop_constraint(
        "fk_classified_bridge_correlation", "classified_transactions", type_="foreignkey"
    )
