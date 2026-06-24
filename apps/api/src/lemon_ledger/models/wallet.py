import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from lemon_ledger.db.base import Base, UUIDPrimaryKeyMixin
from lemon_ledger.models._constraints import CHAIN_SQL

_CHAINS = CHAIN_SQL
_ROLES = "role IN ('vest','live','stake','nft','cold','bridge','other')"
_ADDR_LOWER = "address = lower(address)"


class Wallet(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "wallets"
    __table_args__ = (
        UniqueConstraint("user_id", "chain", "address", name="uq_wallets_user_chain_address"),
        CheckConstraint(_CHAINS, name="ck_wallets_chain"),
        CheckConstraint(_ROLES, name="ck_wallets_role"),
        CheckConstraint(_ADDR_LOWER, name="ck_wallets_address_lowercase"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    chain: Mapped[str] = mapped_column(Text)
    address: Mapped[str] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(Text)
    added_via: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_block: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_classified_block: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
