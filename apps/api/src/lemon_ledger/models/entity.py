import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from lemon_ledger.db.base import Base, UUIDPrimaryKeyMixin

_ENTITY_TYPES = "type IN ('personal','s-corp','llc-passthrough','partnership','sole-prop')"
_BASIS_METHODS = "default_basis_method IN ('fifo','specific_id')"


class Entity(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "entities"
    __table_args__ = (
        CheckConstraint(_ENTITY_TYPES, name="ck_entities_type"),
        CheckConstraint(_BASIS_METHODS, name="ck_entities_default_basis_method"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)
    tax_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    formation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    fiscal_year_end: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_basis_method: Mapped[str] = mapped_column(Text, server_default="fifo")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
