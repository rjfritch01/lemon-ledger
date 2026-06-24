"""Pricing persistence helpers.

upsert_historical_prices is the single write path for automated price data.
It refuses to overwrite rows whose source='manual' so user-entered overrides
survive nightly re-runs and backfill restarts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass
class HistoricalPriceRow:
    chain: str
    token_id: str
    day_timestamp: int  # Unix timestamp of UTC midnight for this calendar day
    average_price_usd: Decimal
    data_points: int
    confidence: int
    source: str  # 'oracle' | 'coingecko' | 'manual'


def day_to_timestamp(day: date) -> int:
    """Convert a calendar date to the UTC-midnight Unix timestamp for that day."""
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())


def upsert_historical_prices(session: Session, rows: list[HistoricalPriceRow]) -> int:
    """INSERT ... ON CONFLICT DO UPDATE for automated price data.

    Manual overrides (source='manual') are never overwritten — the WHERE clause
    on the DO UPDATE guard ensures idempotency across re-runs and backfill restarts.

    Returns the number of rows actually inserted or updated.
    """
    if not rows:
        return 0

    stmt = text(
        """
        INSERT INTO historical_prices
            (chain, token_id, day_timestamp,
             average_price_usd, data_points, confidence, source, updated_at)
        VALUES
            (:chain, :token_id, :day_timestamp,
             :average_price_usd, :data_points, :confidence, :source, CURRENT_TIMESTAMP)
        ON CONFLICT (chain, token_id, day_timestamp)
        DO UPDATE SET
            average_price_usd = EXCLUDED.average_price_usd,
            data_points       = EXCLUDED.data_points,
            confidence        = EXCLUDED.confidence,
            source            = EXCLUDED.source,
            updated_at        = CURRENT_TIMESTAMP
        WHERE historical_prices.source != 'manual'
        """
    )
    params = [
        {
            "chain": r.chain,
            "token_id": r.token_id,
            "day_timestamp": r.day_timestamp,
            "average_price_usd": str(r.average_price_usd),
            "data_points": r.data_points,
            "confidence": r.confidence,
            "source": r.source,
        }
        for r in rows
    ]
    from sqlalchemy.engine import CursorResult

    result: CursorResult[tuple[object, ...]] = session.execute(stmt, params)  # type: ignore[assignment]
    session.commit()
    return result.rowcount or 0
