import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from lemon_ledger.db.base import Base

_CLASSIFICATIONS = "classification IN ('include','spam','pending-review')"


class UserTokenClassification(Base):
    __tablename__ = "user_token_classifications"
    __table_args__ = (CheckConstraint(_CLASSIFICATIONS, name="ck_utc_classification"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    token_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("token_registry.id", ondelete="RESTRICT"), primary_key=True
    )
    classification: Mapped[str] = mapped_column(Text, server_default="pending-review")
    classified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
