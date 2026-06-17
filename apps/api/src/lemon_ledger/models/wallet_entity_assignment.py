import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from lemon_ledger.db.base import Base, UUIDPrimaryKeyMixin

_CLASSIFICATIONS = (
    "classification IN ("
    "'initial-assignment','capital-contribution','sale','gift','loan','reassignment')"
)


class WalletEntityAssignment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "wallet_entity_assignments"
    __table_args__ = (
        CheckConstraint(_CLASSIFICATIONS, name="ck_wea_classification"),
        Index(
            "uq_wea_wallet_current",
            "wallet_id",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
        ),
    )

    wallet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wallets.id", ondelete="RESTRICT"))
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="RESTRICT"))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    classification: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
