"""ORM model for classification_audit_log.

Append-only audit record for every action on a pending_classifications row
(resolve, reclassify, dismiss).  Mirrors bridge_audit_log's structure exactly —
disjoint domain, identical shape.  Do not unify without an explicit decision.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from lemon_ledger.db.base import Base, UUIDPrimaryKeyMixin


class ClassificationAuditLog(UUIDPrimaryKeyMixin, Base):
    """One row per resolve / reclassify / dismiss action."""

    __tablename__ = "classification_audit_log"
    __table_args__ = (Index("ix_cls_audit_pending_id", "pending_id"),)

    pending_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pending_classifications.id", ondelete="RESTRICT"),
        nullable=False,
    )
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    before_state: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    after_state: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
