"""ORM model for the pending_classifications staging table.

A row is created by the counterparty-detection pass for each transfer that
cannot be auto-resolved to relocate-internal (same-entity wallet move).
The resolve service transitions it through the state machine and stamps the
engine signal onto the relevant ClassifiedTransaction leg(s).

kind ↔ chosen_classification validity is enforced in the resolve service
(domain layer), NOT via a cross-column DB CHECK — see enums.ChosenClassification
for the allowed sets.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

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
from sqlalchemy.orm import Mapped, mapped_column

from lemon_ledger.db.base import Base, UUIDPrimaryKeyMixin
from lemon_ledger.models.enums import (
    AdjustmentCode,
    ChosenClassification,
    PendingClassificationKind,
    PendingClassificationState,
)

_KINDS = ", ".join(f"'{k.value}'" for k in PendingClassificationKind)
_STATES = ", ".join(f"'{k.value}'" for k in PendingClassificationState)
_CHOSEN = ", ".join(f"'{k.value}'" for k in ChosenClassification)
_RESOLVED_BY = "'user','rule'"
_ADJ_CODES = ", ".join(f"'{k.value}'" for k in AdjustmentCode)


class PendingClassification(UUIDPrimaryKeyMixin, Base):
    """One row per detected transfer leg requiring user (or rule) resolution."""

    __tablename__ = "pending_classifications"
    __table_args__ = (
        UniqueConstraint("logical_transfer_key", name="uq_pending_cls_transfer_key"),
        Index("ix_pending_cls_user_state", "user_id", "state"),
        CheckConstraint(f"kind IN ({_KINDS})", name="ck_pending_cls_kind"),
        CheckConstraint(f"state IN ({_STATES})", name="ck_pending_cls_state"),
        CheckConstraint(
            f"chosen_classification IS NULL OR chosen_classification IN ({_CHOSEN})",
            name="ck_pending_cls_chosen",
        ),
        CheckConstraint(
            f"resolved_by IS NULL OR resolved_by IN ({_RESOLVED_BY})",
            name="ck_pending_cls_resolved_by",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    # Identity of the transfer event — stable key used for dedup on re-sync.
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    logical_transfer_key: Mapped[str] = mapped_column(Text, nullable=False)
    chain: Mapped[str] = mapped_column(Text, nullable=False)
    tx_hash: Mapped[str] = mapped_column(Text, nullable=False)
    transfer_index: Mapped[int] = mapped_column(Integer, nullable=False)
    token_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("token_registry.id", ondelete="RESTRICT"), nullable=False
    )
    canonical_asset: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(78, 18), nullable=False)

    # As-of-date wallet/entity snapshot (set at detection time per Decision 2C).
    from_wallet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wallets.id", ondelete="RESTRICT"), nullable=False
    )
    from_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="RESTRICT"), nullable=False
    )
    to_wallet_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("wallets.id", ondelete="RESTRICT"), nullable=True
    )
    to_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("entities.id", ondelete="RESTRICT"), nullable=True
    )
    to_address: Mapped[str | None] = mapped_column(Text, nullable=True)

    # State machine.
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="needs_classification")
    chosen_classification: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        # raw UUID; no FK — rule registry is Phase 2.
        nullable=True
    )
    dismiss_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps.
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
