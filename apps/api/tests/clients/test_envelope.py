import pytest

from lemon_ledger.clients.envelope import parse_list_envelope
from lemon_ledger.clients.exceptions import ChainFatalError, ChainRateLimited

# ── status == "1" ─────────────────────────────────────────────────────────────


def test_status1_list_returns_rows() -> None:
    payload = {
        "status": "1",
        "message": "OK",
        "result": [{"hash": "0xabc", "value": "100"}, {"hash": "0xdef", "value": "200"}],
    }
    result = parse_list_envelope(payload)
    assert result == [{"hash": "0xabc", "value": "100"}, {"hash": "0xdef", "value": "200"}]


def test_status1_empty_list_returns_empty() -> None:
    payload = {"status": "1", "message": "OK", "result": []}
    assert parse_list_envelope(payload) == []


def test_status1_non_list_raises_response_error() -> None:
    payload = {"status": "1", "message": "OK", "result": "some string"}
    with pytest.raises(ChainFatalError, match="expected list"):
        parse_list_envelope(payload)


def test_status1_result_is_dict_raises() -> None:
    payload = {"status": "1", "message": "OK", "result": {"foo": "bar"}}
    with pytest.raises(ChainFatalError):
        parse_list_envelope(payload)


def test_row_values_coerced_to_str() -> None:
    payload = {"status": "1", "message": "OK", "result": [{"block": 12345, "active": True}]}
    result = parse_list_envelope(payload)
    assert result == [{"block": "12345", "active": "True"}]


# ── empty / not-found responses ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "No transactions found",
        "No Token transfers found",
        "No internal transactions found",
        "No logs found",
        "No records found",
        "No tokens found",
    ],
)
def test_no_found_messages_return_empty(message: str) -> None:
    payload = {"status": "0", "message": message, "result": ""}
    assert parse_list_envelope(payload) == []


def test_result_empty_list_returns_empty() -> None:
    payload = {"status": "0", "message": "NOTOK", "result": []}
    assert parse_list_envelope(payload) == []


def test_result_str_startswith_no_returns_empty() -> None:
    payload = {"status": "0", "message": "NOTOK", "result": "No data found for query"}
    assert parse_list_envelope(payload) == []


# ── rate-limit signals ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message,result",
    [
        ("Max rate limit reached", ""),
        ("", "Max rate limit reached"),
        ("rate limit", ""),
        ("too many requests", ""),
        ("NOTOK", "Rate limit reached, slow down"),
    ],
)
def test_rate_limit_raises_transient(message: str, result: str) -> None:
    payload = {"status": "0", "message": message, "result": result}
    with pytest.raises(ChainRateLimited):
        parse_list_envelope(payload)


# ── unknown error ─────────────────────────────────────────────────────────────


def test_unrecognised_status0_raises_response_error() -> None:
    payload = {"status": "0", "message": "Something went wrong", "result": "error"}
    with pytest.raises(ChainFatalError):
        parse_list_envelope(payload)


def test_non_dict_payload_raises() -> None:
    with pytest.raises(ChainFatalError, match="Expected dict"):
        parse_list_envelope(["not", "a", "dict"])
