"""Tests for CoinMarketCapClient — secondary external price source."""

from decimal import Decimal

import httpx
import pytest
import respx

from lemon_ledger.clients.coinmarketcap import (
    CoinMarketCapClient,
    PricerRateLimited,
    PricerTransientError,
)

_BASE = "https://pro-api.coinmarketcap.com"
_KEY = "test-cmc-key"


def _client(max_retries: int = 1) -> CoinMarketCapClient:
    return CoinMarketCapClient(http=httpx.Client(), api_key=_KEY, max_retries=max_retries)


def _quote_response(cmc_id: int, price: float) -> dict:  # type: ignore[type-arg]
    return {
        "data": {
            str(cmc_id): {
                "id": cmc_id,
                "quote": {"USD": {"price": price}},
            }
        }
    }


# ── quote_usd ─────────────────────────────────────────────────────────────────


@respx.mock
def test_quote_usd_happy_path_returns_exact_decimal() -> None:
    respx.get(f"{_BASE}/v2/cryptocurrency/quotes/latest").mock(
        return_value=httpx.Response(200, json=_quote_response(29949, 0.0412))
    )
    result = _client().quote_usd(29949)
    assert result == Decimal("0.0412")
    assert isinstance(result, Decimal)
    assert not isinstance(result, float)


@respx.mock
def test_quote_usd_sends_api_key_header() -> None:
    route = respx.get(f"{_BASE}/v2/cryptocurrency/quotes/latest").mock(
        return_value=httpx.Response(200, json=_quote_response(1, 45000.0))
    )
    _client().quote_usd(1)
    assert route.calls.last.request.headers.get("X-CMC_PRO_API_KEY") == _KEY


@respx.mock
def test_quote_usd_missing_id_returns_none() -> None:
    respx.get(f"{_BASE}/v2/cryptocurrency/quotes/latest").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    assert _client().quote_usd(99999) is None


@respx.mock
def test_quote_usd_string_id_works() -> None:
    respx.get(f"{_BASE}/v2/cryptocurrency/quotes/latest").mock(
        return_value=httpx.Response(200, json=_quote_response(29949, 0.05))
    )
    result = _client().quote_usd("29949")
    assert result == Decimal("0.05")


@respx.mock
def test_quote_usd_404_returns_none() -> None:
    respx.get(f"{_BASE}/v2/cryptocurrency/quotes/latest").mock(return_value=httpx.Response(404))
    assert _client().quote_usd(1) is None


@respx.mock
def test_quote_usd_429_raises_rate_limited() -> None:
    respx.get(f"{_BASE}/v2/cryptocurrency/quotes/latest").mock(return_value=httpx.Response(429))
    with pytest.raises(PricerRateLimited):
        _client(max_retries=1).quote_usd(1)


@respx.mock
def test_quote_usd_500_raises_transient() -> None:
    respx.get(f"{_BASE}/v2/cryptocurrency/quotes/latest").mock(return_value=httpx.Response(500))
    with pytest.raises(PricerTransientError):
        _client(max_retries=1).quote_usd(1)
