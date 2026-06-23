"""Tests for CoinGeckoClient — primary external price source."""

from datetime import date
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from lemon_ledger.clients.coingecko import (
    CoinGeckoClient,
    PricerRateLimited,
    PricerTransientError,
)

_BASE = "https://api.coingecko.com/api/v3"
_CONTRACT = "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


def _client(max_retries: int = 1) -> CoinGeckoClient:
    return CoinGeckoClient(http=httpx.Client(), max_retries=max_retries)


def _client_with_key(key: str = "test-key") -> CoinGeckoClient:
    return CoinGeckoClient(http=httpx.Client(), api_key=key, max_retries=1)


# ── coin_price_usd ────────────────────────────────────────────────────────────


@respx.mock
def test_coin_price_usd_happy_path_returns_exact_decimal() -> None:
    respx.get(f"{_BASE}/simple/price").mock(
        return_value=httpx.Response(200, json={"lemon-2": {"usd": 0.042}})
    )
    result = _client().coin_price_usd("lemon-2")
    assert result == Decimal("0.042")
    # Confirm the value went through Decimal(str(...)) — not a float
    assert isinstance(result, Decimal)
    assert not isinstance(result, float)


@respx.mock
def test_coin_price_usd_unknown_id_returns_none() -> None:
    respx.get(f"{_BASE}/simple/price").mock(return_value=httpx.Response(200, json={}))
    assert _client().coin_price_usd("not-a-real-coin") is None


@respx.mock
def test_coin_price_usd_sends_demo_api_key_header() -> None:
    route = respx.get(f"{_BASE}/simple/price").mock(
        return_value=httpx.Response(200, json={"lemon-2": {"usd": 1.0}})
    )
    _client_with_key("my-key").coin_price_usd("lemon-2")
    assert route.called
    assert route.calls.last.request.headers.get("x-cg-demo-api-key") == "my-key"


@respx.mock
def test_coin_price_usd_404_returns_none() -> None:
    respx.get(f"{_BASE}/simple/price").mock(return_value=httpx.Response(404))
    assert _client().coin_price_usd("lemon-2") is None


# ── coin_history_usd ──────────────────────────────────────────────────────────


@respx.mock
def test_coin_history_usd_formats_date_as_dd_mm_yyyy() -> None:
    route = respx.get(f"{_BASE}/coins/lemon-2/history").mock(
        return_value=httpx.Response(
            200,
            json={"market_data": {"current_price": {"usd": 0.0385}}},
        )
    )
    result = _client().coin_history_usd("lemon-2", date(2024, 3, 15))
    assert result == Decimal("0.0385")
    assert isinstance(result, Decimal)
    sent_params: dict[str, Any] = dict(route.calls.last.request.url.params)
    assert sent_params["date"] == "15-03-2024"


@respx.mock
def test_coin_history_usd_missing_market_data_returns_none() -> None:
    respx.get(f"{_BASE}/coins/lemon-2/history").mock(
        return_value=httpx.Response(200, json={"id": "lemon-2"})
    )
    assert _client().coin_history_usd("lemon-2", date(2024, 1, 1)) is None


@respx.mock
def test_coin_history_usd_404_returns_none() -> None:
    respx.get(f"{_BASE}/coins/nonexistent/history").mock(return_value=httpx.Response(404))
    assert _client().coin_history_usd("nonexistent", date(2024, 1, 1)) is None


# ── token_price_usd ───────────────────────────────────────────────────────────


def test_token_price_usd_lemonchain_returns_none_without_http_call() -> None:
    """Lemonchain has no CoinGecko platform — must short-circuit immediately."""
    client = _client()
    with respx.mock:
        # No routes registered: any HTTP call would raise an error
        result = client.token_price_usd("lemonchain", _CONTRACT)
    assert result is None


@respx.mock
def test_token_price_usd_bsc_happy_path() -> None:
    """Proves the dormant BSC path is implemented and functional."""
    contract = _CONTRACT.lower()
    respx.get(f"{_BASE}/simple/token_price/binance-smart-chain").mock(
        return_value=httpx.Response(
            200,
            json={contract: {"usd": 1.23}},
        )
    )
    result = _client().token_price_usd("bsc", _CONTRACT)
    assert result == Decimal("1.23")
    assert isinstance(result, Decimal)


@respx.mock
def test_token_price_usd_bsc_missing_contract_returns_none() -> None:
    respx.get(f"{_BASE}/simple/token_price/binance-smart-chain").mock(
        return_value=httpx.Response(200, json={})
    )
    assert _client().token_price_usd("bsc", _CONTRACT) is None


# ── retry / 429 ───────────────────────────────────────────────────────────────


@respx.mock
def test_coin_price_usd_429_retries_then_succeeds() -> None:
    """First call returns 429; tenacity retries; second call succeeds."""
    respx.get(f"{_BASE}/simple/price").mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(200, json={"lemon-2": {"usd": 0.1}}),
        ]
    )
    client = CoinGeckoClient(http=httpx.Client(), max_retries=2)
    # Patch time.sleep so the exponential-backoff wait is instant in tests.
    with patch("time.sleep", MagicMock()):
        result = client.coin_price_usd("lemon-2")
    assert result == Decimal("0.1")


@respx.mock
def test_coin_price_usd_429_exhausted_raises() -> None:
    respx.get(f"{_BASE}/simple/price").mock(return_value=httpx.Response(429))
    with pytest.raises(PricerRateLimited):
        _client(max_retries=1).coin_price_usd("lemon-2")


@respx.mock
def test_coin_price_usd_500_raises_transient() -> None:
    respx.get(f"{_BASE}/simple/price").mock(return_value=httpx.Response(500))
    with pytest.raises(PricerTransientError):
        _client(max_retries=1).coin_price_usd("lemon-2")
