"""Chainlink-compatible oracle price client.

PriceDataFeed       — Chainlink AggregatorV3 reader (spot price / round data).
OracleClient        — multi-feed dispatcher (spot, daily-avg, health, history).
OracleDailyAverage  — per-day record returned by get_daily_averages_history.
oracle_key          — resolves a token's oracle lookup address (zero for native LEMX).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from lemon_ledger.clients.evm.provider import EVMProvider

log = logging.getLogger(__name__)

# ABI selector for latestRoundData() — keccak256("latestRoundData()") first 4 bytes
_LATEST_ROUND_DATA = "0xfeaf968c"

_DEFAULT_STALENESS_S = 3_600  # 1 hour


class OraclePriceStale(Exception):
    """Oracle price was not updated within the expected staleness window."""


class OracleTokenNotSupported(Exception):
    """No oracle price feed is configured for this token."""


# ABI selector for getHistory(address,uint256) — first 4 bytes of keccak256
_GET_HISTORY = "0x6a3d4b5c"

# Zero address — used as the oracle key for native (non-contract) tokens like LEMX
_ZERO_ADDRESS = "0x" + "0" * 40

# Oracle price decimals (all feeds publish 8-decimal prices)
_ORACLE_PRICE_DECIMALS = 8


class _TokenLike(Protocol):
    """Minimal interface the oracle dispatcher needs from a token row."""

    symbol: str
    contract_address: str | None


@dataclass(frozen=True)
class OracleDailyAverage:
    """One row of on-chain daily-average history."""

    day_timestamp: int  # Unix timestamp of UTC midnight for this day
    average_price: Decimal  # already scaled by from_oracle_price
    data_points: int
    confidence: int


def oracle_key(token: _TokenLike) -> str:
    """Return the address the oracle uses to key this token's price data.

    Native tokens with no deployed contract (e.g. LEMX on Lemonchain) use the
    zero address as the oracle lookup key.
    """
    return token.contract_address or _ZERO_ADDRESS


@dataclass(frozen=True)
class RoundData:
    round_id: int
    price: Decimal
    started_at: int
    updated_at: int
    answered_in_round: int


@dataclass
class OracleHealth:
    paused: bool = False
    emergency: bool = False
    seeding_complete: bool = False
    feeds_ok: dict[str, bool] = field(default_factory=dict)


class PriceDataFeed:
    """Read-only wrapper around a Chainlink AggregatorV3-compatible oracle.

    Calls latestRoundData() on-chain and returns the answer as a Decimal
    scaled by the oracle's published decimal precision.
    """

    def __init__(
        self,
        provider: EVMProvider,
        contract_address: str,
        *,
        decimals: int = 8,
        staleness_window: int = _DEFAULT_STALENESS_S,
    ) -> None:
        self._provider = provider
        self._contract = contract_address
        self._decimals = decimals
        self._staleness_window = staleness_window

    def _latest_round(self) -> RoundData:
        """Decode latestRoundData() from the ABI-encoded hex response."""
        raw_hex = self._provider.eth_call(self._contract, _LATEST_ROUND_DATA)
        payload = bytes.fromhex(raw_hex[2:])  # strip 0x prefix
        # ABI layout: (uint80, int256, uint256, uint256, uint80) — each 32 bytes
        round_id = int.from_bytes(payload[0:32], "big", signed=False)
        answer = int.from_bytes(payload[32:64], "big", signed=True)
        started_at = int.from_bytes(payload[64:96], "big", signed=False)
        updated_at = int.from_bytes(payload[96:128], "big", signed=False)
        answered_in_round = int.from_bytes(payload[128:160], "big", signed=False)
        return RoundData(
            round_id=round_id,
            price=Decimal(answer).scaleb(-self._decimals),
            started_at=started_at,
            updated_at=updated_at,
            answered_in_round=answered_in_round,
        )

    def latest_price(self) -> Decimal:
        """Return the latest oracle price as a human-scale Decimal (no staleness check)."""
        return self._latest_round().price

    def spot_price(self) -> Decimal:
        """Return the latest price, raising OraclePriceStale if the feed is stale."""
        rd = self._latest_round()
        now = int(time.time())
        if rd.updated_at == 0 or (now - rd.updated_at) > self._staleness_window:
            raise OraclePriceStale(
                f"Feed {self._contract}: updatedAt={rd.updated_at}, "
                f"age={(now - rd.updated_at)}s, window={self._staleness_window}s"
            )
        return rd.price

    def daily_average(self) -> Decimal | None:
        """Proxy spot price for FMV calculations. Returns None if the price is 0 (invalid)."""
        try:
            rd = self._latest_round()
        except Exception:
            return None
        return rd.price if rd.price != Decimal(0) else None


class OracleClient:
    """Multi-feed oracle dispatcher.

    Keyed by token symbol so PricingService can route any TokenRow to its feed
    without knowing contract addresses at the service layer.
    """

    def __init__(self, feeds: dict[str, PriceDataFeed]) -> None:
        self._feeds = feeds  # symbol -> PriceDataFeed

    def _feed_for(self, token: _TokenLike) -> PriceDataFeed:
        feed = self._feeds.get(token.symbol)
        if feed is None:
            raise OracleTokenNotSupported(f"No oracle feed for {token.symbol!r}")
        return feed

    def get_spot_price(self, token: _TokenLike) -> Decimal:
        """Current spot price. Raises OraclePriceStale or OracleTokenNotSupported."""
        return self._feed_for(token).spot_price()

    def get_daily_average(self, token: _TokenLike) -> Decimal | None:
        """Daily-average proxy. Returns None if unsupported or price is 0."""
        try:
            return self._feed_for(token).daily_average()
        except OracleTokenNotSupported:
            return None

    def get_daily_averages_history(
        self,
        token: _TokenLike,
        max_entries: int = 30,
    ) -> list[OracleDailyAverage]:
        """Return up to ``max_entries`` days of daily-average history for this token.

        Calls ``getHistory(address token, uint256 count)`` on the oracle contract
        and decodes the packed ABI response.  The oracle keeps only the last 30
        calendar days on-chain; older history must come from the event-log backfill.

        ABI response layout per entry (5 × 32 bytes):
          slot 0: dayTimestamp (uint64)
          slot 1: dailyAverage (uint128, raw oracle units)
          slot 2: dataPoints (uint32)
          slot 3: confidence (uint32)
        Entries are returned oldest-first; we reverse to get most-recent-first.
        """
        feed = self._feed_for(token)
        addr = oracle_key(token)

        # Encode getHistory(address,uint256): selector + zero-padded args
        addr_clean = addr[2:].lower().zfill(64)
        count_hex = hex(max_entries)[2:].zfill(64)
        calldata = _GET_HISTORY + addr_clean + count_hex

        raw_hex = feed._provider.eth_call(feed._contract, calldata)
        payload = bytes.fromhex(raw_hex[2:])

        # ABI-encoded dynamic array: offset (32) + length (32) + entries
        if len(payload) < 64:
            return []
        entry_count = int.from_bytes(payload[32:64], "big", signed=False)
        entries: list[OracleDailyAverage] = []
        entry_size = 4 * 32  # 4 slots × 32 bytes
        base = 64
        for i in range(min(entry_count, max_entries)):
            off = base + i * entry_size
            if off + entry_size > len(payload):
                break
            day_ts = int.from_bytes(payload[off : off + 32], "big", signed=False)
            avg_raw = int.from_bytes(payload[off + 32 : off + 64], "big", signed=False)
            data_pts = int.from_bytes(payload[off + 64 : off + 96], "big", signed=False)
            conf = int.from_bytes(payload[off + 96 : off + 128], "big", signed=False)
            entries.append(
                OracleDailyAverage(
                    day_timestamp=day_ts,
                    average_price=Decimal(avg_raw).scaleb(-_ORACLE_PRICE_DECIMALS),
                    data_points=data_pts,
                    confidence=conf,
                )
            )
        return entries

    def get_health(self) -> OracleHealth:
        """Probe every registered feed and return aggregate health."""
        feeds_ok: dict[str, bool] = {}
        seeding_complete = True
        for symbol, feed in self._feeds.items():
            try:
                rd = feed._latest_round()
                feeds_ok[symbol] = True
                if rd.updated_at == 0:
                    seeding_complete = False
            except Exception:
                feeds_ok[symbol] = False
                seeding_complete = False
        return OracleHealth(
            paused=False,
            emergency=False,
            seeding_complete=seeding_complete and bool(self._feeds),
            feeds_ok=feeds_ok,
        )
