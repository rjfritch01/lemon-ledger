from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, Integer, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from lemon_ledger.db.base import Base

_CHAINS = "chain IN ('lemonchain','bsc')"
_SOURCES = "source IN ('oracle','coingecko','manual')"


class HistoricalPrice(Base):
    """One daily-average USD price row per (chain, token_id, day_timestamp).

    ``day_timestamp`` is the Unix timestamp of UTC midnight for the calendar day.
    Using a BigInteger timestamp (not a DATE column) keeps the PK fully in the
    domain of integers and avoids timezone-interpretation drift across clients.

    The ``source`` column tracks provenance so the upsert guard can refuse to
    overwrite a 'manual' override with an automated oracle or CoinGecko value.
    """

    __tablename__ = "historical_prices"
    __table_args__ = (
        CheckConstraint(_CHAINS, name="ck_historical_prices_chain"),
        CheckConstraint(_SOURCES, name="ck_historical_prices_source"),
    )

    # Composite PK — no surrogate UUID needed for this append-only table
    chain: Mapped[str] = mapped_column(Text, primary_key=True)
    token_id: Mapped[str] = mapped_column(Text, primary_key=True)
    day_timestamp: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    average_price_usd: Mapped[object] = mapped_column(Numeric(38, 18), nullable=False)
    data_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        "created_at",
        default=func.now(),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        "updated_at",
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
    )
