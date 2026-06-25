"""Create bridge_correlations, custody_addresses, bridge_audit_log.

Revision ID: d5e6f7a2b3c4
Revises: c4d5e6f7a2b3
Create Date: 2026-06-24 00:07:00.000000

Creates the three bridge tables.  The circular FK between classified_transactions
and bridge_correlations is resolved in the next migration (0008) using ALTER TABLE
ADD CONSTRAINT ... with use_alter=True so both tables exist before either references
the other.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5e6f7a2b3c4"
down_revision: str | None = "c4d5e6f7a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BRIDGE_STATUSES = "'confirmed','needs_confirmation','rejected','unmatched'"
_CONFIDENCE_LEVELS = "'high','medium','low'"
_CUSTODY_RECOGNITIONS = "'recognized','inferred'"
_USER_RESOLUTIONS = "'bridge-pending','sale','third-party','other'"
_RESOLVED_BYS = "'auto','user'"
_CUSTODY_SOURCES = "'curated','learned'"


def upgrade() -> None:
    # ── bridge_correlations ───────────────────────────────────────────────────
    op.create_table(
        "bridge_correlations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "logical_asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("logical_assets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # Outflow and inflow FKs to classified_transactions are intentionally
        # deferred: the circular FK is added in migration 0008.
        sa.Column(
            "outflow_classified_event_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "inflow_classified_event_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("confidence_level", sa.Text(), nullable=True),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("matched_custody_address", sa.Text(), nullable=True),
        sa.Column("custody_recognition", sa.Text(), nullable=True),
        sa.Column("time_delta_seconds", sa.Integer(), nullable=True),
        sa.Column("amount_delta_bps", sa.Integer(), nullable=True),
        sa.Column("user_resolution", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # CHECK constraints
        sa.CheckConstraint(f"status IN ({_BRIDGE_STATUSES})", name="ck_bridge_status"),
        sa.CheckConstraint(
            f"confidence_level IS NULL OR confidence_level IN ({_CONFIDENCE_LEVELS})",
            name="ck_bridge_confidence_level",
        ),
        sa.CheckConstraint(
            f"custody_recognition IS NULL OR custody_recognition IN ({_CUSTODY_RECOGNITIONS})",
            name="ck_bridge_custody_recognition",
        ),
        sa.CheckConstraint(
            f"user_resolution IS NULL OR user_resolution IN ({_USER_RESOLUTIONS})",
            name="ck_bridge_user_resolution",
        ),
        sa.CheckConstraint(
            f"resolved_by IS NULL OR resolved_by IN ({_RESOLVED_BYS})",
            name="ck_bridge_resolved_by",
        ),
        sa.CheckConstraint(
            "outflow_classified_event_id IS NOT NULL OR inflow_classified_event_id IS NOT NULL",
            name="ck_bridge_at_least_one_leg",
        ),
    )

    op.create_index("ix_bridge_correlations_user", "bridge_correlations", ["user_id"])
    op.create_index(
        "ix_bridge_correlations_logical_asset",
        "bridge_correlations",
        ["logical_asset_id"],
    )
    op.create_index(
        "ix_bridge_correlations_outflow",
        "bridge_correlations",
        ["outflow_classified_event_id"],
    )
    op.create_index(
        "ix_bridge_correlations_inflow",
        "bridge_correlations",
        ["inflow_classified_event_id"],
    )
    op.create_index("ix_bridge_correlations_status", "bridge_correlations", ["status"])
    # Partial unique index: one live pair per leg combination.
    op.create_index(
        "uq_bridge_legs",
        "bridge_correlations",
        ["outflow_classified_event_id", "inflow_classified_event_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'rejected'"),
    )

    # ── custody_addresses ─────────────────────────────────────────────────────
    op.create_table(
        "custody_addresses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("chain", sa.Text(), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("recognition", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("confirmed_pair_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unique_user_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("chain", "address", name="uq_custody_chain_addr"),
        sa.CheckConstraint(
            f"recognition IN ({_CUSTODY_RECOGNITIONS})",
            name="ck_custody_recognition",
        ),
        sa.CheckConstraint(
            f"source IN ({_CUSTODY_SOURCES})",
            name="ck_custody_source",
        ),
    )
    op.create_index("ix_custody_chain", "custody_addresses", ["chain"])
    op.create_index("ix_custody_address", "custody_addresses", ["address"])

    # ── bridge_audit_log ──────────────────────────────────────────────────────
    op.create_table(
        "bridge_audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "correlation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bridge_correlations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("before_state", postgresql.JSONB(), nullable=True),
        sa.Column("after_state", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_bridge_audit_corr", "bridge_audit_log", ["correlation_id"])


def downgrade() -> None:
    op.drop_table("bridge_audit_log")
    op.drop_table("custody_addresses")
    op.drop_index("uq_bridge_legs", table_name="bridge_correlations")
    op.drop_index("ix_bridge_correlations_status", table_name="bridge_correlations")
    op.drop_index("ix_bridge_correlations_inflow", table_name="bridge_correlations")
    op.drop_index("ix_bridge_correlations_outflow", table_name="bridge_correlations")
    op.drop_index("ix_bridge_correlations_logical_asset", table_name="bridge_correlations")
    op.drop_index("ix_bridge_correlations_user", table_name="bridge_correlations")
    op.drop_table("bridge_correlations")
