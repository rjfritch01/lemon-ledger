import uuid
from datetime import UTC, datetime
from typing import Any

from lemon_ledger.clients.exceptions import BlockscoutResponseError


def map_transaction(wallet_id: uuid.UUID, chain: str, rec: dict[str, str]) -> dict[str, Any]:
    return {
        "wallet_id": wallet_id,
        "chain": chain,
        "block_number": int(rec["blockNumber"]),
        "tx_hash": rec["hash"],
        "occurred_at": datetime.fromtimestamp(int(rec["timeStamp"]), tz=UTC),
        "value": int(rec["value"]),
        "raw": rec,
    }


def map_token_transfer(wallet_id: uuid.UUID, chain: str, rec: dict[str, str]) -> dict[str, Any]:
    return {
        "wallet_id": wallet_id,
        "chain": chain,
        "block_number": int(rec["blockNumber"]),
        "tx_hash": rec["hash"],
        "occurred_at": datetime.fromtimestamp(int(rec["timeStamp"]), tz=UTC),
        "value": int(rec["value"]),
        "log_index": int(rec["logIndex"]),
        "contract_address": rec["contractAddress"].lower(),
        "raw": rec,
    }


def map_internal_tx(wallet_id: uuid.UUID, chain: str, rec: dict[str, str]) -> dict[str, Any]:
    trace_id = rec.get("traceId") or rec.get("trace_id") or ""
    if not trace_id:
        raise BlockscoutResponseError(
            f"Internal tx missing trace_id for tx_hash={rec.get('hash', '?')!r}"
        )
    return {
        "wallet_id": wallet_id,
        "chain": chain,
        "block_number": int(rec["blockNumber"]),
        "tx_hash": rec["hash"],
        "occurred_at": datetime.fromtimestamp(int(rec["timeStamp"]), tz=UTC),
        "value": int(rec["value"]),
        "trace_id": trace_id,
        "raw": rec,
    }
