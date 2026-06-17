"""Mapper unit tests — no DB, no network."""

import uuid
from datetime import UTC, datetime

import pytest

from lemon_ledger.clients.exceptions import ChainFatalError
from lemon_ledger.ingestion.mappers import map_internal_tx, map_token_transfer, map_transaction

_WID = uuid.uuid4()
_CHAIN = "lemonchain"

_TX_REC: dict[str, str] = {
    "blockNumber": "1000",
    "hash": "0xabc",
    "timeStamp": "1700000000",
    "value": "1000000000000000000",
}

_TT_REC: dict[str, str] = {
    "blockNumber": "1001",
    "hash": "0xdef",
    "timeStamp": "1700000001",
    "value": "500000000000000000",
    "logIndex": "3",
    "contractAddress": "0xCAFEBABEcafebabeCAFEBABEcafebabeCAFEBABE",
}

_IT_REC: dict[str, str] = {
    "blockNumber": "1002",
    "hash": "0xfed",
    "timeStamp": "1700000002",
    "value": "0",
    "traceId": "call_0",
}


def test_map_transaction_fields() -> None:
    row = map_transaction(_WID, _CHAIN, _TX_REC)
    assert row["wallet_id"] == _WID
    assert row["chain"] == _CHAIN
    assert row["block_number"] == 1000
    assert row["tx_hash"] == "0xabc"
    assert row["occurred_at"] == datetime.fromtimestamp(1700000000, tz=UTC)
    assert row["value"] == 1000000000000000000
    assert row["raw"] is _TX_REC


def test_map_transaction_value_is_int() -> None:
    row = map_transaction(_WID, _CHAIN, _TX_REC)
    assert isinstance(row["value"], int)


def test_map_transaction_missing_field_raises() -> None:
    with pytest.raises(KeyError):
        map_transaction(_WID, _CHAIN, {"blockNumber": "1", "hash": "0x1", "timeStamp": "0"})


def test_map_token_transfer_fields() -> None:
    row = map_token_transfer(_WID, _CHAIN, _TT_REC)
    assert row["log_index"] == 3
    assert row["contract_address"] == "0xcafebabeCAFEBABEcafebabeCAFEBABEcafebabe".lower()
    assert row["value"] == 500000000000000000


def test_map_token_transfer_contract_address_lowercased() -> None:
    row = map_token_transfer(_WID, _CHAIN, _TT_REC)
    assert row["contract_address"] == row["contract_address"].lower()


def test_map_internal_tx_fields() -> None:
    row = map_internal_tx(_WID, _CHAIN, _IT_REC)
    assert row["trace_id"] == "call_0"
    assert row["value"] == 0


def test_map_internal_tx_alt_field_name() -> None:
    rec = dict(_IT_REC)
    del rec["traceId"]
    rec["trace_id"] = "call_1"
    row = map_internal_tx(_WID, _CHAIN, rec)
    assert row["trace_id"] == "call_1"


def test_map_internal_tx_missing_trace_id_raises() -> None:
    rec = {k: v for k, v in _IT_REC.items() if k != "traceId"}
    with pytest.raises(ChainFatalError, match="trace_id"):
        map_internal_tx(_WID, _CHAIN, rec)


def test_map_internal_tx_empty_trace_id_raises() -> None:
    rec = dict(_IT_REC)
    rec["traceId"] = ""
    with pytest.raises(ChainFatalError):
        map_internal_tx(_WID, _CHAIN, rec)
