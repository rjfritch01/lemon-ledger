"""Tests for historical_backfill — covers all spec-required scenarios.

All tests are pure-Python (no Docker, no Redis, no DB) except the
upsert-guard test which uses an in-memory SQLite session.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import fakeredis
import pytest

from lemon_ledger.pricing.historical_backfill import (
    DAILY_AVG_TOPIC,
    INITIAL_CHUNK,
    RedisCursor,
    _NullCursor,
    _parse_event,
    _start_block,
    _upsert_events,
    backfill,
    fetch_day,
)
from lemon_ledger.pricing.repository import HistoricalPriceRow, upsert_historical_prices
from lemon_ledger.pricing.types import TokenRow

# ── helpers ────────────────────────────────────────────────────────────────────

_ZERO_ADDR = "0x" + "0" * 40
_LEMX_ID = "lemx-id"
_ORACLE_CONTRACT = "0x" + "c" * 40
_ORACLE_DECIMALS = 8


def _tok(symbol: str = "LEMX") -> TokenRow:
    return TokenRow(
        token_id=f"{symbol.lower()}-id",
        symbol=symbol,
        category="ecosystem-native",
        contract_address=None if symbol == "LEMX" else "0x" + "a" * 40,
        chain="lemonchain",
        tier=1,
        decimals=18,
    )


def _make_chain_client(
    latest_block: int = 50_000,
    logs: list[dict[str, str]] | None = None,
    *,
    block_by_time: int | None = None,
) -> MagicMock:
    client = MagicMock()
    client.get_latest_block.return_value = latest_block
    client.get_logs.return_value = logs or []
    client.get_block_by_time.return_value = block_by_time if block_by_time is not None else 1000
    return client


def _make_registry(
    token_id: str | None = _LEMX_ID,
    *,
    unknown_address: bool = False,
) -> MagicMock:
    registry = MagicMock()
    registry.id_for_address.return_value = None if unknown_address else token_id
    registry.historical_price.return_value = None
    return registry


def _make_session() -> MagicMock:
    """Return a session mock that accepts execute() without complaint."""
    session = MagicMock()
    result = MagicMock()
    result.rowcount = 1
    session.execute.return_value = result
    return session


def _encode_event(
    token_addr: str,
    day_ts: int,
    avg_raw: int,
    data_pts: int = 10,
    confidence: int = 95,
) -> dict[str, str]:
    """Build a DailyAverageFinalized log entry in the expected ABI format."""
    padded_addr = token_addr[2:].zfill(64)
    data = (
        day_ts.to_bytes(32, "big")
        + avg_raw.to_bytes(32, "big")
        + data_pts.to_bytes(32, "big")
        + confidence.to_bytes(32, "big")
    ).hex()
    return {
        "topics": f"{DAILY_AVG_TOPIC},{padded_addr}",
        "data": "0x" + data,
    }


# ── _start_block ───────────────────────────────────────────────────────────────


def test_start_block_calls_get_block_by_time() -> None:
    """_start_block must call get_block_by_time — no hardcoded constant."""
    client = _make_chain_client(block_by_time=12_345)
    result = _start_block(client)
    assert result == 12_345
    client.get_block_by_time.assert_called_once()
    # Verify the call is to get_block_by_time with closest="before"
    _, kwargs = client.get_block_by_time.call_args
    assert kwargs.get("closest") == "before" or client.get_block_by_time.call_args[0][1] == "before"


def test_start_block_uses_genesis_datetime() -> None:
    """_start_block must pass a datetime for 2025-09-01 UTC to get_block_by_time."""
    client = _make_chain_client(block_by_time=999)
    _start_block(client)
    dt_arg = client.get_block_by_time.call_args[0][0]
    assert dt_arg.year == 2025
    assert dt_arg.month == 9
    assert dt_arg.day == 1
    assert dt_arg.tzinfo is not None


# ── _parse_event ───────────────────────────────────────────────────────────────


def test_parse_event_decodes_correctly() -> None:
    day_ts = int(datetime(2025, 10, 1, tzinfo=UTC).timestamp())
    avg_raw = 4_200_000  # 0.042 USDC with 8 decimals
    entry = _encode_event(_ZERO_ADDR, day_ts, avg_raw)
    parsed = _parse_event(entry)
    assert parsed is not None
    addr, ts, raw, pts, conf = parsed
    assert addr == _ZERO_ADDR
    assert ts == day_ts
    assert raw == avg_raw


def test_parse_event_invalid_data_returns_none() -> None:
    assert _parse_event({"topics": DAILY_AVG_TOPIC, "data": "0x00"}) is None


def test_parse_event_missing_topics_returns_none() -> None:
    assert _parse_event({"data": "0x" + "00" * 128}) is None


# ── _upsert_events ─────────────────────────────────────────────────────────────


def test_upsert_events_zero_address_maps_to_lemx() -> None:
    """A zero-address event must resolve to the LEMX token_id via registry."""
    day_ts = int(datetime(2025, 10, 1, tzinfo=UTC).timestamp())
    entry = _encode_event(_ZERO_ADDR, day_ts, 4_200_000)
    registry = _make_registry(token_id=_LEMX_ID)
    session = _make_session()

    count = _upsert_events([entry], registry=registry, session=session)

    assert count == 1
    registry.id_for_address.assert_called_once_with("lemonchain", _ZERO_ADDR)


def test_upsert_events_unknown_token_skipped() -> None:
    """Events for unregistered token addresses must be silently skipped."""
    day_ts = int(datetime(2025, 10, 1, tzinfo=UTC).timestamp())
    unknown_addr = "0x" + "f" * 40
    entry = _encode_event(unknown_addr, day_ts, 4_200_000)
    registry = _make_registry(unknown_address=True)
    session = _make_session()

    count = _upsert_events([entry], registry=registry, session=session)

    assert count == 0
    session.execute.assert_not_called()


def test_upsert_events_no_float_in_price() -> None:
    """average_price_usd in the upserted row must be a Decimal, never a float."""
    day_ts = int(datetime(2025, 10, 1, tzinfo=UTC).timestamp())
    entry = _encode_event(_ZERO_ADDR, day_ts, 4_200_000)
    registry = _make_registry()
    rows_captured: list[list[HistoricalPriceRow]] = []

    def _capture_upsert(session: object, rows: list[HistoricalPriceRow]) -> int:
        rows_captured.append(rows)
        return len(rows)

    with patch(
        "lemon_ledger.pricing.historical_backfill.upsert_historical_prices",
        side_effect=_capture_upsert,
    ):
        _upsert_events([entry], registry=registry, session=_make_session())

    assert rows_captured, "Expected upsert to be called"
    for row in rows_captured[0]:
        assert isinstance(row.average_price_usd, Decimal), "price must be Decimal, not float"


# ── upsert guard — manual source is never overwritten ─────────────────────────


def test_upsert_guard_manual_not_overwritten() -> None:
    """A manual-source row must survive an oracle upsert (WHERE source != 'manual')."""
    # Use an in-memory SQLite session to exercise the real SQL
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.connect() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE historical_prices (
                    chain TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    day_timestamp INTEGER NOT NULL,
                    average_price_usd TEXT NOT NULL,
                    data_points INTEGER NOT NULL DEFAULT 0,
                    confidence INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL,
                    created_at TEXT,
                    updated_at TEXT,
                    PRIMARY KEY (chain, token_id, day_timestamp),
                    CHECK (source IN ('oracle','coingecko','manual'))
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO historical_prices "
                "(chain, token_id, day_timestamp, average_price_usd, source) "
                "VALUES ('lemonchain', 'lemx-id', 1727740800, '0.099', 'manual')"
            )
        )
        conn.commit()

    Session = sessionmaker(engine)
    with Session() as session:
        oracle_row = HistoricalPriceRow(
            chain="lemonchain",
            token_id="lemx-id",
            day_timestamp=1727740800,
            average_price_usd=Decimal("0.042"),
            data_points=10,
            confidence=95,
            source="oracle",
        )
        upsert_historical_prices(session, [oracle_row])

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT average_price_usd, source FROM historical_prices WHERE token_id='lemx-id'")
        ).fetchone()
        assert row is not None
        assert row[1] == "manual", "source must remain 'manual'"
        assert row[0] == "0.099", "manual price must not be overwritten by oracle"


# ── adaptive chunking ─────────────────────────────────────────────────────────


def test_chunk_halves_on_value_error_and_retries_same_lo() -> None:
    """On ValueError, chunk halves and the SAME lo is retried (no progress loss)."""
    call_count = 0
    lo_values: list[int] = []

    def _get_logs(
        address: str, *, from_block: int, to_block: int | str, topic0: str | None = None
    ) -> list[dict[str, str]]:
        nonlocal call_count
        call_count += 1
        lo_values.append(from_block)
        if call_count == 1:
            raise ValueError("eth_getLogs range too wide")
        return []

    client = MagicMock()
    client.get_latest_block.return_value = INITIAL_CHUNK - 1  # one chunk total
    client.get_block_by_time.return_value = 0
    client.get_logs.side_effect = _get_logs

    backfill(
        client,
        _ORACLE_CONTRACT,
        _make_registry(),
        _make_session(),
        cursor_store=_NullCursor(),
    )

    assert call_count >= 2, "Expected at least one retry after ValueError"
    # The SAME lo must be used on the first retry
    assert lo_values[0] == lo_values[1], "lo must not advance after ValueError"


def test_chunk_grows_after_success() -> None:
    """Chunk doubles after each successful call up to MAX_CHUNK."""
    chunks: list[int] = []

    def _get_logs(
        address: str, *, from_block: int, to_block: int | str, topic0: str | None = None
    ) -> list[dict[str, str]]:
        chunks.append(int(to_block) - from_block + 1)
        return []

    client = MagicMock()
    client.get_latest_block.return_value = INITIAL_CHUNK * 4  # force several iterations
    client.get_block_by_time.return_value = 0
    client.get_logs.side_effect = _get_logs

    backfill(
        client,
        _ORACLE_CONTRACT,
        _make_registry(),
        _make_session(),
        cursor_store=_NullCursor(),
    )

    # First call uses INITIAL_CHUNK, second should use 2×INITIAL_CHUNK
    assert len(chunks) >= 2
    assert chunks[1] == INITIAL_CHUNK * 2


def test_chunk_below_min_raises() -> None:
    """ValueError when chunk is already at MIN_CHUNK must propagate."""
    client = MagicMock()
    client.get_latest_block.return_value = 999
    client.get_block_by_time.return_value = 0
    client.get_logs.side_effect = ValueError("too many results")

    with pytest.raises(ValueError):
        backfill(
            client,
            _ORACLE_CONTRACT,
            _make_registry(),
            _make_session(),
            cursor_store=_NullCursor(),
        )


# ── cursor resume ─────────────────────────────────────────────────────────────


def test_cursor_resume_no_duplicate_rows() -> None:
    """Resuming from a cursor skips already-processed blocks."""
    r = fakeredis.FakeRedis()
    cursor = RedisCursor(r)
    cursor.save(3000)  # simulate crash after block 3000

    call_log: list[int] = []

    def _get_logs(address: str, *, from_block: int, **kw: object) -> list[dict[str, str]]:
        call_log.append(from_block)
        return []

    client = MagicMock()
    client.get_latest_block.return_value = 4000
    client.get_logs.side_effect = _get_logs

    backfill(
        client,
        _ORACLE_CONTRACT,
        _make_registry(),
        _make_session(),
        cursor_store=cursor,
    )

    # cursor saves hi, so resume starts at hi (block 3000 may be re-scanned once;
    # idempotent upsert means no duplicate rows)
    assert all(lo >= 3000 for lo in call_log), "Must not re-scan blocks before cursor position"


# ── nightly_oracle_sync ────────────────────────────────────────────────────────


def test_nightly_sync_continues_past_unsupported_token() -> None:
    """OracleTokenNotSupported for one token must not stop processing others."""
    from lemon_ledger.clients.oracle import OracleTokenNotSupported
    from lemon_ledger.pricing.tasks import nightly_oracle_sync

    lemx = _tok("LEMX")
    wlemx = _tok("WLEMX")
    tokens = [lemx, wlemx]

    oracle = MagicMock()
    oracle.get_health.return_value = MagicMock(
        paused=False, emergency=False, seeding_complete=True, feeds_ok={"LEMX": True}
    )

    def _get_history(token: TokenRow, max_entries: int = 30) -> list[object]:
        if token.symbol == "WLEMX":
            raise OracleTokenNotSupported("no WLEMX feed")
        return []  # LEMX returns empty for simplicity

    oracle.get_daily_averages_history.side_effect = _get_history

    registry = MagicMock()
    registry.tier1_lemonchain.return_value = tokens

    result = nightly_oracle_sync(_registry=registry, _oracle=oracle, _session=_make_session())

    assert "WLEMX" in result["tokens_skipped"]
    assert result["tokens_processed"] == 1  # LEMX was processed


def test_nightly_sync_alerts_on_degraded_oracle(caplog: pytest.LogCaptureFixture) -> None:
    """A degraded oracle (paused or failing feeds) must emit an ERROR log."""
    from lemon_ledger.pricing.tasks import nightly_oracle_sync

    oracle = MagicMock()
    oracle.get_health.return_value = MagicMock(
        paused=True,
        emergency=False,
        seeding_complete=False,
        feeds_ok={"LEMX": False},
    )
    oracle.get_daily_averages_history.return_value = []

    registry = MagicMock()
    registry.tier1_lemonchain.return_value = [_tok("LEMX")]

    with caplog.at_level(logging.ERROR, logger="lemon_ledger.pricing.tasks"):
        nightly_oracle_sync(_registry=registry, _oracle=oracle, _session=_make_session())

    assert any(r.levelname == "ERROR" for r in caplog.records), (
        "Expected an ERROR log for degraded oracle"
    )


# ── fetch_day ─────────────────────────────────────────────────────────────────


def test_fetch_day_calls_get_block_by_time_for_window() -> None:
    """fetch_day must call get_block_by_time twice (day start + day end)."""
    client = _make_chain_client(block_by_time=1000)
    registry = _make_registry()
    session = _make_session()

    fetch_day(
        _tok("LEMX"),
        date(2025, 10, 1),
        client,
        _ORACLE_CONTRACT,
        registry,
        session,
    )

    assert client.get_block_by_time.call_count == 2


def test_fetch_day_lemx_gap_falls_back_to_coingecko() -> None:
    """When oracle has no event for LEMX on that day, CoinGecko history is used."""
    client = _make_chain_client(logs=[])  # no oracle events
    coingecko = MagicMock()
    coingecko.coin_history_usd.return_value = Decimal("0.039")

    result = fetch_day(
        _tok("LEMX"),
        date(2025, 10, 1),
        client,
        _ORACLE_CONTRACT,
        _make_registry(),
        _make_session(),
        coingecko=coingecko,
    )

    assert result == Decimal("0.039")
    coingecko.coin_history_usd.assert_called_once()


def test_fetch_day_l2_gap_returns_none() -> None:
    """For non-LEMX tokens with no oracle event, fetch_day returns None."""
    client = _make_chain_client(logs=[])

    result = fetch_day(
        _tok("LEMON"),
        date(2025, 10, 1),
        client,
        _ORACLE_CONTRACT,
        _make_registry(token_id="lemon-id"),
        _make_session(),
        coingecko=None,
    )

    assert result is None


def test_fetch_day_returns_oracle_price_when_event_found() -> None:
    """When an oracle event is found for the day, its price is returned."""
    day_ts = int(datetime(2025, 10, 1, tzinfo=UTC).timestamp())
    avg_raw = 4_200_000  # 0.042 with 8 decimal places
    entry = _encode_event(_ZERO_ADDR, day_ts, avg_raw)

    client = _make_chain_client(logs=[entry])
    registry = _make_registry()
    session = _make_session()

    result = fetch_day(
        _tok("LEMX"),
        date(2025, 10, 1),
        client,
        _ORACLE_CONTRACT,
        registry,
        session,
    )

    expected = Decimal(avg_raw).scaleb(-8)
    assert result == expected
    assert isinstance(result, Decimal)
