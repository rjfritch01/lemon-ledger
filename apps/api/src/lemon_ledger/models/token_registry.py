import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lemon_ledger.db.base import Base

_CHAINS = "chain IN ('lemonchain','bsc')"
_TIERS = "tier IN (1,2)"
_CATEGORIES = (
    "category IN ("
    "'ecosystem-l2','ecosystem-stablecoin','ecosystem-native',"
    "'external-stablecoin','external-major','external-other')"
)


class TokenRegistry(Base):
    __tablename__ = "token_registry"
    __table_args__ = (
        UniqueConstraint("chain", "contract_address", name="uq_token_registry_chain_address"),
        CheckConstraint(_CHAINS, name="ck_token_registry_chain"),
        CheckConstraint(_TIERS, name="ck_token_registry_tier"),
        CheckConstraint(_CATEGORIES, name="ck_token_registry_category"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    chain: Mapped[str] = mapped_column(Text)
    contract_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    symbol: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    decimals: Mapped[int] = mapped_column(SmallInteger)
    tier: Mapped[int] = mapped_column(SmallInteger)
    category: Mapped[str] = mapped_column(Text)
    pricing_source_primary: Mapped[str | None] = mapped_column(Text, nullable=True)
    pricing_source_fallback: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_deflationary: Mapped[bool] = mapped_column(Boolean, server_default="false")
    max_supply: Mapped[Any] = mapped_column(Numeric, nullable=True)
    project_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
