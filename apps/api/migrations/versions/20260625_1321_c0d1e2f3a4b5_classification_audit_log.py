"""1.9 Stage 3: classification_audit_log — append-only audit table for pending_classifications.

Mirrors bridge_audit_log's structure (same column shape, same conventions).
Disjoint domain from bridge_audit_log — do not unify without explicit chat decision.

Revision ID: c0d1e2f3a4b5
Revises: a8b9c0d1e2f3
Create Date: 2026-06-25 13:21:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c0d1e2f3a4b5"
down_revision: str | None = "a8b9c0d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "classification_audit_log",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "pending_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("pending_classifications.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("rule_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("before_state", JSONB(), nullable=True),
        sa.Column("after_state", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_cls_audit_pending_id", "classification_audit_log", ["pending_id"])


def downgrade() -> None:
    op.drop_index("ix_cls_audit_pending_id", table_name="classification_audit_log")
    op.drop_table("classification_audit_log")
