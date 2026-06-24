"""BurnAddress — trusted burn sink addresses for deflationary tokens."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from uuid_utils.compat import uuid7

from lemon_ledger.db.base import Base

_CONFIDENCE = "confidence IN ('universal','discovered','confirmed')"


class BurnAddress(Base):
    """A known or discovered burn address for a deflationary L2 token.

    confidence:
      universal  — EVM universal sinks (0x0, 0xdead); apply to ALL deflationary tokens
      discovered — surfaced by heuristic (high outflow, no inflow); needs human gate
      confirmed  — operator-verified; safe to book BURN classification
    """

    __tablename__ = "burn_addresses"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    address: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # NULL token_id = universal sink (applies to all deflationary tokens)
    token_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("token_registry.id", ondelete="RESTRICT"), nullable=True
    )
    confidence: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (CheckConstraint(_CONFIDENCE, name="ck_burn_addr_confidence"),)
