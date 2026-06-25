"""Bridge correlation and custody address models.

BridgeCorrelation  — one row per detected (or unmatched) outflow/inflow pair.
CustodyAddress     — curated + learned bridge protocol addresses.
BridgeAuditLog     — append-only resolution history.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from lemon_ledger.db.base import Base, UUIDPrimaryKeyMixin

# ── StrEnums ──────────────────────────────────────────────────────────────────


class BridgeStatus(StrEnum):
    CONFIRMED = "confirmed"
    NEEDS_CONFIRMATION = "needs_confirmation"
    REJECTED = "rejected"
    UNMATCHED = "unmatched"


class ConfidenceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CustodyRecognition(StrEnum):
    RECOGNIZED = "recognized"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class ResolvedBy(StrEnum):
    AUTO = "auto"
    USER = "user"


class UserResolution(StrEnum):
    BRIDGE_PENDING = "bridge-pending"
    SALE = "sale"
    THIRD_PARTY = "third-party"
    OTHER = "other"


class BridgeTreatment(StrEnum):
    RELOCATE = "relocate"
    DISPOSITION = "disposition"


# ── CHECK strings derived from enums ─────────────────────────────────────────

_BRIDGE_STATUSES = ", ".join(f"'{v.value}'" for v in BridgeStatus)
_CONFIDENCE_LEVELS = ", ".join(f"'{v.value}'" for v in ConfidenceLevel)
_CUSTODY_RECOGNITIONS = ", ".join(f"'{v.value}'" for v in CustodyRecognition)
_RESOLVED_BYS = ", ".join(f"'{v.value}'" for v in ResolvedBy)
_USER_RESOLUTIONS = ", ".join(f"'{v.value}'" for v in UserResolution)


# ── BridgeCorrelation ─────────────────────────────────────────────────────────


class BridgeCorrelation(UUIDPrimaryKeyMixin, Base):
    """One row per bridge hypothesis (matched pair or unmatched singleton)."""

    __tablename__ = "bridge_correlations"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_BRIDGE_STATUSES})",
            name="ck_bridge_status",
        ),
        CheckConstraint(
            f"confidence_level IS NULL OR confidence_level IN ({_CONFIDENCE_LEVELS})",
            name="ck_bridge_confidence_level",
        ),
        CheckConstraint(
            f"custody_recognition IS NULL OR custody_recognition IN ({_CUSTODY_RECOGNITIONS})",
            name="ck_bridge_custody_recognition",
        ),
        CheckConstraint(
            f"user_resolution IS NULL OR user_resolution IN ({_USER_RESOLUTIONS})",
            name="ck_bridge_user_resolution",
        ),
        CheckConstraint(
            f"resolved_by IS NULL OR resolved_by IN ({_RESOLVED_BYS})",
            name="ck_bridge_resolved_by",
        ),
        CheckConstraint(
            "outflow_classified_event_id IS NOT NULL OR inflow_classified_event_id IS NOT NULL",
            name="ck_bridge_at_least_one_leg",
        ),
        Index("ix_bridge_correlations_user", "user_id"),
        Index("ix_bridge_correlations_logical_asset", "logical_asset_id"),
        Index("ix_bridge_correlations_outflow", "outflow_classified_event_id"),
        Index("ix_bridge_correlations_inflow", "inflow_classified_event_id"),
        Index("ix_bridge_correlations_status", "status"),
        # Partial unique: only one live pairing per leg combination (rejected pairs can recur).
        Index(
            "uq_bridge_legs",
            "outflow_classified_event_id",
            "inflow_classified_event_id",
            unique=True,
            postgresql_where="status <> 'rejected'",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    logical_asset_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("logical_assets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    outflow_classified_event_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("classified_transactions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    inflow_classified_event_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("classified_transactions.id", ondelete="RESTRICT"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(Text, nullable=False)
    confidence_level: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    matched_custody_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    custody_recognition: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_delta_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_delta_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)

    user_resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ── CustodyAddress ────────────────────────────────────────────────────────────

_CUSTODY_SOURCES = "'curated','learned'"
_CUSTODY_REC_VALUES = ", ".join(
    f"'{v.value}'" for v in CustodyRecognition if v != CustodyRecognition.UNKNOWN
)


class CustodyAddress(UUIDPrimaryKeyMixin, Base):
    """Known bridge protocol addresses; absence of a row = 'unknown'."""

    __tablename__ = "custody_addresses"
    __table_args__ = (
        UniqueConstraint("chain", "address", name="uq_custody_chain_addr"),
        CheckConstraint(
            f"recognition IN ({_CUSTODY_REC_VALUES})",
            name="ck_custody_recognition",
        ),
        CheckConstraint(
            f"source IN ({_CUSTODY_SOURCES})",
            name="ck_custody_source",
        ),
        Index("ix_custody_chain", "chain"),
        Index("ix_custody_address", "address"),
    )

    chain: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    recognition: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_pair_count: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    unique_user_count: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ── BridgeAuditLog ────────────────────────────────────────────────────────────


class BridgeAuditLog(UUIDPrimaryKeyMixin, Base):
    """Append-only audit record for every bridge resolution action."""

    __tablename__ = "bridge_audit_log"
    __table_args__ = (Index("ix_bridge_audit_corr", "correlation_id"),)

    correlation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bridge_correlations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
