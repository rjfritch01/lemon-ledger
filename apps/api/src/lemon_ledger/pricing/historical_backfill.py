"""DailyAverageFinalized event-log backfill.

Rationale
---------
The oracle contract keeps only ~30 days of daily averages in on-chain storage.
The DailyAverageFinalized event log is permanent.  This module crawls the log
from the oracle's genesis (2025-09-01) to the chain head and populates
historical_prices for every finalization event.

Every date→block conversion goes through ChainClient.get_block_by_time.
No block numbers are hardcoded anywhere in this module.

Adaptive chunking
-----------------
The crawler starts at INITIAL_CHUNK blocks per request.  If the node returns
a too-many-results / range-too-large error (ValueError), it halves the chunk
and RETRIES THE SAME lo (no progress lost).  On success it doubles the chunk
up to MAX_CHUNK for large empty ranges.

Cursor-store
------------
A simple key-value abstraction (CursorStore Protocol) backed by a Redis key.
Pass a null cursor to start fresh; pass a Redis-backed one for resumable runs.
The CLI flag --resume wires the Redis cursor.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy.orm import Session

from lemon_ledger.clients.base import ChainClient
from lemon_ledger.clients.coingecko import CoinGeckoClient
from lemon_ledger.clients.oracle import oracle_key
from lemon_ledger.pricing.external_ids import LEMX_COINGECKO_ID
from lemon_ledger.pricing.repository import (
    HistoricalPriceRow,
    upsert_historical_prices,
)
from lemon_ledger.pricing.types import TokenRegistryRepo, TokenRow
from lemon_ledger.pricing.units import from_oracle_price

log = logging.getLogger(__name__)

# keccak256("DailyAverageFinalized(address,uint64,uint128,uint32,uint32)")
DAILY_AVG_TOPIC = "0x8c5b6c7da6d97571e40fce04cf4af7de399e13e5fd6c4ddf9d7ac1ba8f03a6e"

# Oracle genesis — no finalization events exist before Lemonchain launch (2025-09-01 UTC)
_GENESIS_DATE = datetime(2025, 9, 1, tzinfo=UTC)

# Adaptive chunk bounds
INITIAL_CHUNK: int = 5_000
MIN_CHUNK: int = 200
MAX_CHUNK: int = 20_000

# Oracle price precision (all feeds use 8 decimal places)
_ORACLE_DECIMALS = 8


class CursorStore(Protocol):
    """Minimal key-value store for the backfill resume cursor."""

    def load(self) -> int | None: ...
    def save(self, block: int) -> None: ...


class _NullCursor:
    """Always starts from the beginning (no resume support)."""

    def load(self) -> int | None:
        return None

    def save(self, block: int) -> None:
        pass


class RedisCursor:
    """Redis-backed cursor for resumable backfill runs."""

    def __init__(self, redis: Any, key: str = "backfill:cursor:lemonchain") -> None:
        self._r: Any = redis
        self._key = key

    def load(self) -> int | None:
        # redis.get() returns bytes | None for a sync Redis client
        val: bytes | None = self._r.get(self._key)
        if val is None:
            return None
        return int(val)

    def save(self, block: int) -> None:
        self._r.set(self._key, str(block))


def _start_block(chain_client: ChainClient) -> int:
    """Return the block just before the oracle genesis date.

    All date→block conversions go through get_block_by_time; no constant is used.
    """
    return chain_client.get_block_by_time(_GENESIS_DATE, closest="before")


def _parse_event(
    log_entry: dict[str, str] | dict[str, str | list[str]],
) -> tuple[str, int, int, int, int] | None:
    """Decode a DailyAverageFinalized log entry.

    ABI: DailyAverageFinalized(address indexed token, uint64 dayTimestamp,
                                uint128 dailyAverage, uint32 dataPoints,
                                uint32 confidence)

    The indexed 'token' address is in topics[1]; the remaining args are ABI-encoded
    in the 'data' field (3-4 × 32-byte slots).
    """
    try:
        raw_topics = log_entry.get("topics")
        if isinstance(raw_topics, str):
            topics: list[str] = [t.strip() for t in raw_topics.split(",")]
        elif isinstance(raw_topics, list):
            topics = [str(t) for t in raw_topics]
        else:
            return None

        if len(topics) < 2:
            return None

        token_addr = "0x" + topics[1][-40:]

        data_raw = log_entry.get("data", "")
        data_hex = str(data_raw)
        if data_hex.startswith("0x"):
            data_hex = data_hex[2:]
        data = bytes.fromhex(data_hex)
        if len(data) < 96:
            return None

        day_ts = int.from_bytes(data[0:32], "big", signed=False)
        avg_raw = int.from_bytes(data[32:64], "big", signed=False)
        data_pts = int.from_bytes(data[64:96], "big", signed=False)
        confidence = int.from_bytes(data[96:128], "big", signed=False) if len(data) >= 128 else 0

        return token_addr.lower(), day_ts, avg_raw, data_pts, confidence
    except Exception:
        log.debug("Failed to parse log entry: %r", log_entry)
        return None


def _upsert_events(
    logs: list[dict[str, str | list[str]]] | list[dict[str, str]],
    *,
    registry: TokenRegistryRepo,
    session: Session,
    chain: str = "lemonchain",
) -> int:
    """Decode DailyAverageFinalized events and upsert into historical_prices.

    Zero-address events map to the native LEMX token via registry.id_for_address.
    Unknown token addresses are skipped silently.
    """
    rows: list[HistoricalPriceRow] = []
    for entry in logs:
        parsed = _parse_event(entry)
        if parsed is None:
            continue
        token_addr, day_ts, avg_raw, data_pts, conf = parsed
        token_id = registry.id_for_address(chain, token_addr)
        if token_id is None:
            log.debug("Unknown oracle token address %s — skipping event", token_addr)
            continue
        rows.append(
            HistoricalPriceRow(
                chain=chain,
                token_id=token_id,
                day_timestamp=day_ts,
                average_price_usd=from_oracle_price(avg_raw, _ORACLE_DECIMALS),
                data_points=data_pts,
                confidence=conf,
                source="oracle",
            )
        )
    if rows:
        upsert_historical_prices(session, rows)
    return len(rows)


def backfill(
    chain_client: ChainClient,
    oracle_contract: str,
    registry: TokenRegistryRepo,
    session: Session,
    *,
    cursor_store: CursorStore | None = None,
    chain: str = "lemonchain",
) -> None:
    """Crawl DailyAverageFinalized events from genesis to chain head.

    Adaptive chunking: halves on ValueError (too-many-results), doubles on
    success up to MAX_CHUNK.  Cursor is saved after each successful chunk so a
    crash can be resumed without re-scanning already-processed blocks.
    """
    store: CursorStore = cursor_store or _NullCursor()

    lo = store.load()
    if lo is None:
        lo = _start_block(chain_client)
        log.info("backfill starting from block %d (genesis date %s)", lo, _GENESIS_DATE)
    else:
        log.info("backfill resuming from cursor block %d", lo)

    head = chain_client.get_latest_block()
    chunk = INITIAL_CHUNK
    total_rows = 0

    while lo <= head:
        hi = min(lo + chunk - 1, head)
        try:
            logs = chain_client.get_logs(
                oracle_contract,
                from_block=lo,
                to_block=hi,
                topic0=DAILY_AVG_TOPIC,
            )
        except ValueError as exc:
            if chunk > MIN_CHUNK:
                chunk = max(MIN_CHUNK, chunk // 2)
                log.warning(
                    "backfill: too-many-results at lo=%d, halving chunk to %d (%s)",
                    lo,
                    chunk,
                    exc,
                )
                continue  # retry SAME lo with smaller chunk
            raise

        count = _upsert_events(logs, registry=registry, session=session, chain=chain)
        total_rows += count
        store.save(hi)
        log.debug("backfill: blocks %d-%d -> %d events", lo, hi, count)

        lo = hi + 1
        chunk = min(MAX_CHUNK, chunk * 2)

    log.info("backfill complete: %d rows upserted, head=%d", total_rows, head)


def fetch_day(
    token: TokenRow,
    day: date,
    chain_client: ChainClient,
    oracle_contract: str,
    registry: TokenRegistryRepo,
    session: Session,
    coingecko: CoinGeckoClient | None = None,
    *,
    chain: str = "lemonchain",
) -> Decimal | None:
    """On-demand single-day fetch — the live-fallback hook wired into PricingService.

    Narrows to the day's block range via get_block_by_time (two calls), then
    filters DailyAverageFinalized events for this token.  Falls back to CoinGecko
    for LEMX when the oracle has no event for that day.  Returns None for other
    tokens with no oracle event.
    """
    day_start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)

    lo = chain_client.get_block_by_time(day_start, closest="after")
    hi = chain_client.get_block_by_time(day_end, closest="before")

    tok_key = oracle_key(token).lower()
    raw_logs: list[dict[str, str]] = chain_client.get_logs(
        oracle_contract,
        from_block=lo,
        to_block=hi,
        topic0=DAILY_AVG_TOPIC,
    )

    matching = [e for e in raw_logs if _token_matches(e, tok_key)]

    if matching:
        _upsert_events(matching, registry=registry, session=session, chain=chain)
        parsed = _parse_event(matching[-1])
        if parsed is not None:
            _, _, avg_raw, _, _ = parsed
            return from_oracle_price(avg_raw, _ORACLE_DECIMALS)

    # No oracle event — LEMX falls back to CoinGecko historical price
    if token.symbol == "LEMX" and coingecko is not None:
        return coingecko.coin_history_usd(LEMX_COINGECKO_ID, day)

    return None


def _token_matches(log_entry: dict[str, str] | dict[str, str | list[str]], tok_key: str) -> bool:
    """Return True if the log's indexed token address matches tok_key."""
    parsed = _parse_event(log_entry)
    return parsed is not None and parsed[0] == tok_key
