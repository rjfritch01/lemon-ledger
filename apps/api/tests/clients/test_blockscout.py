from typing import Any

import httpx
import pytest
import respx

from lemon_ledger.clients.blockscout import BlockscoutClient
from lemon_ledger.clients.exceptions import (
    BlockscoutResponseError,
    BlockscoutTransientError,
    BlockscoutWindowExceeded,
)
from lemon_ledger.clients.rate_limit import NullRateLimiter

BASE_URL = "https://test.explorer.io/api"
_ADDR = "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


def _make_client(page_size: int = 10, max_retries: int = 1) -> BlockscoutClient:
    return BlockscoutClient(
        BASE_URL,
        http=httpx.Client(),
        rate_limiter=NullRateLimiter(),
        page_size=page_size,
        max_retries=max_retries,
    )


def _ok(result: Any) -> dict[str, Any]:
    return {"status": "1", "message": "OK", "result": result}


# ── _get HTTP status mapping ──────────────────────────────────────────────────


@respx.mock
def test_get_200_returns_json() -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=_ok([])))
    client = _make_client()
    payload = client._get({"module": "account", "action": "txlist"})
    assert payload["status"] == "1"


@respx.mock
def test_get_429_raises_transient() -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(429))
    client = _make_client()
    with pytest.raises(BlockscoutTransientError):
        client._get({"module": "account", "action": "txlist"})


@respx.mock
def test_get_500_raises_transient() -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(500))
    client = _make_client()
    with pytest.raises(BlockscoutTransientError):
        client._get({"module": "account", "action": "txlist"})


@respx.mock
def test_get_4xx_raises_response_error() -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(404))
    client = _make_client()
    with pytest.raises(BlockscoutResponseError):
        client._get({"module": "account", "action": "txlist"})


@respx.mock
def test_get_timeout_raises_transient() -> None:
    respx.get(BASE_URL).mock(side_effect=httpx.TimeoutException("timed out"))
    client = _make_client()
    with pytest.raises(BlockscoutTransientError, match="timed out"):
        client._get({"module": "account", "action": "txlist"})


@respx.mock
def test_get_transport_error_raises_transient() -> None:
    respx.get(BASE_URL).mock(side_effect=httpx.ConnectError("connection refused"))
    client = _make_client()
    with pytest.raises(BlockscoutTransientError, match="Transport"):
        client._get({"module": "account", "action": "txlist"})


@respx.mock
def test_get_429_with_retry_after_header() -> None:
    # Retry-After header is honoured (sleep is called) — just verify no crash.
    respx.get(BASE_URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "0"}))
    client = _make_client()
    with pytest.raises(BlockscoutTransientError):
        client._get({"module": "account", "action": "txlist"})


# ── get_latest_block hex scalar parse ─────────────────────────────────────────


@respx.mock
def test_get_latest_block_parses_hex() -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json={"result": "0x13a65c0"}))
    client = _make_client()
    assert client.get_latest_block() == 0x13A65C0


@respx.mock
def test_get_latest_block_non_string_raises() -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json={"result": 12345}))
    client = _make_client()
    with pytest.raises(BlockscoutResponseError):
        client.get_latest_block()


# ── pagination: short-page stop ───────────────────────────────────────────────


@respx.mock
def test_pagination_short_page_stops() -> None:
    rows = [{"hash": str(i), "value": "0"} for i in range(3)]
    route = respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=_ok(rows)))
    client = _make_client(page_size=10)
    result = list(client.get_transactions(_ADDR))
    assert len(result) == 3
    assert route.call_count == 1  # stopped after the first partial page


@respx.mock
def test_pagination_full_then_short() -> None:
    full_page = [{"hash": str(i), "value": "0"} for i in range(5)]
    short_page = [{"hash": "99", "value": "0"}]
    route = respx.get(BASE_URL).mock(
        side_effect=[
            httpx.Response(200, json=_ok(full_page)),
            httpx.Response(200, json=_ok(short_page)),
        ]
    )
    client = _make_client(page_size=5)
    result = list(client.get_transactions(_ADDR))
    assert len(result) == 6
    assert route.call_count == 2


@respx.mock
def test_pagination_empty_first_page() -> None:
    respx.get(BASE_URL).mock(
        return_value=httpx.Response(
            200,
            json={"status": "0", "message": "No transactions found", "result": ""},
        )
    )
    client = _make_client(page_size=10)
    result = list(client.get_transactions(_ADDR))
    assert result == []


# ── BlockscoutWindowExceeded ───────────────────────────────────────────────────


@respx.mock
def test_window_exceeded_raises_before_oversized_page() -> None:
    # page_size=10_001 means page 1 would already exceed the 10k window
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=_ok([])))
    client = _make_client(page_size=10_001)
    with pytest.raises(BlockscoutWindowExceeded):
        list(client.get_transactions(_ADDR))


@respx.mock
def test_window_exceeded_after_ten_full_pages() -> None:
    # With page_size=1000, pages 1-10 are within the window.
    # The check for page 11 raises before any HTTP request for that page.
    full_page: list[dict[str, str]] = [{"hash": str(i), "value": "0"} for i in range(1000)]
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=_ok(full_page)))
    client = _make_client(page_size=1000)
    with pytest.raises(BlockscoutWindowExceeded):
        list(client.get_transactions(_ADDR))


# ── address lowercasing ───────────────────────────────────────────────────────


@respx.mock
def test_address_lowercased_in_params() -> None:
    route = respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=_ok([])))
    client = _make_client()
    list(client.get_transactions("0xDEADBEEFdeadbeefDEADBEEFdeadbeefDEADBEEF"))
    assert route.called
    request = route.calls[0].request
    assert "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef" in str(request.url)


# ── get_logs single-call (no pagination) ─────────────────────────────────────


@respx.mock
def test_get_logs_returns_list() -> None:
    rows = [{"transactionHash": "0xabc", "topics": "0xtopic0"}]
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=_ok(rows)))
    client = _make_client()
    result = client.get_logs(_ADDR, from_block=100, to_block=200)
    assert len(result) == 1
    assert result[0]["transactionHash"] == "0xabc"


# ── api_key injected when present ────────────────────────────────────────────


@respx.mock
def test_api_key_appended_to_params() -> None:
    route = respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=_ok([])))
    client = BlockscoutClient(
        BASE_URL,
        http=httpx.Client(),
        rate_limiter=NullRateLimiter(),
        api_key="my-secret-key",
        page_size=10,
        max_retries=1,
    )
    list(client.get_transactions(_ADDR))
    assert "my-secret-key" in str(route.calls[0].request.url)


# ── get_token_metadata ────────────────────────────────────────────────────────


@respx.mock
def test_get_token_metadata_returns_dict() -> None:
    meta = {"symbol": "WLEMX", "decimals": "18", "name": "Wrapped LEMX"}
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=_ok(meta)))
    client = _make_client()
    result = client.get_token_metadata(_ADDR)
    assert result["symbol"] == "WLEMX"
    assert result["decimals"] == "18"


@respx.mock
def test_get_token_metadata_non_dict_result_raises() -> None:
    respx.get(BASE_URL).mock(
        return_value=httpx.Response(200, json={"status": "0", "message": "No data", "result": None})
    )
    client = _make_client()
    with pytest.raises(BlockscoutResponseError):
        client.get_token_metadata(_ADDR)


@respx.mock
def test_get_token_metadata_address_lowercased() -> None:
    route = respx.get(BASE_URL).mock(
        return_value=httpx.Response(200, json=_ok({"symbol": "X", "decimals": "18"}))
    )
    client = _make_client()
    client.get_token_metadata("0xDEADBEEFdeadbeefDEADBEEFdeadbeefDEADBEEF")
    assert "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef" in str(route.calls[0].request.url)
