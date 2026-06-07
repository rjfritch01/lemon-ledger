"""Pure sync engine — no Celery import.  All persistence goes through a sync Session."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog
from sqlalchemy import literal_column
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from lemon_ledger.clients.exceptions import BlockscoutWindowExceeded
from lemon_ledger.ingestion.mappers import map_internal_tx, map_token_transfer, map_transaction
from lemon_ledger.models.raw import RawInternalTx, RawTokenTransfer, RawTransaction
from lemon_ledger.models.wallet import Wallet


class SyncClient(Protocol):
    """Minimal interface required by sync_wallet — satisfied by BlockscoutClient and fakes."""

    def get_latest_block(self) -> int: ...

    def get_transactions(
        self, address: str, *, start_block: int = ..., end_block: int | None = ..., sort: str = ...
    ) -> Iterator[dict[str, str]]: ...

    def get_token_transfers(
        self, address: str, *, start_block: int = ..., end_block: int | None = ..., sort: str = ...
    ) -> Iterator[dict[str, str]]: ...

    def get_internal_transactions(
        self, address: str, *, start_block: int = ..., end_block: int | None = ..., sort: str = ...
    ) -> Iterator[dict[str, str]]: ...


@dataclass(frozen=True)
class SyncResult:
    wallet_id: uuid.UUID
    from_block: int
    to_block: int
    transactions: int = 0
    token_transfers: int = 0
    internal_txs: int = 0


def bulk_upsert(
    session: Session,
    model: Any,
    rows: list[dict[str, Any]],
    conflict_cols: list[str],
) -> int:
    if not rows:
        return 0
    stmt: Any = (
        pg_insert(model)
        .values(rows)
        .on_conflict_do_nothing(index_elements=conflict_cols)
        .returning(literal_column("1"))
    )
    result = session.execute(stmt)
    return len(result.all())


def _ingest_chunk(
    session: Session,
    client: SyncClient,
    wallet: Wallet,
    start: int,
    end: int,
) -> Counter[str]:
    """Fetch all three endpoints for [start, end]; recurse if window exceeded."""
    totals: Counter[str] = Counter()
    try:
        tx_rows = [
            map_transaction(wallet.id, wallet.chain, r)
            for r in client.get_transactions(wallet.address, start_block=start, end_block=end)
        ]
        tt_rows = [
            map_token_transfer(wallet.id, wallet.chain, r)
            for r in client.get_token_transfers(wallet.address, start_block=start, end_block=end)
        ]
        it_rows = [
            map_internal_tx(wallet.id, wallet.chain, r)
            for r in client.get_internal_transactions(
                wallet.address, start_block=start, end_block=end
            )
        ]
    except BlockscoutWindowExceeded:
        if end <= start:
            raise
        mid = (start + end) // 2
        totals += _ingest_chunk(session, client, wallet, start, mid)
        totals += _ingest_chunk(session, client, wallet, mid + 1, end)
        return totals

    totals["transactions"] += bulk_upsert(
        session, RawTransaction, tx_rows, ["wallet_id", "tx_hash"]
    )
    totals["token_transfers"] += bulk_upsert(
        session, RawTokenTransfer, tt_rows, ["wallet_id", "tx_hash", "log_index"]
    )
    totals["internal_txs"] += bulk_upsert(
        session, RawInternalTx, it_rows, ["wallet_id", "tx_hash", "trace_id"]
    )
    return totals


def sync_wallet(
    session: Session,
    client: SyncClient,
    wallet: Wallet,
    *,
    confirmations: int,
    chunk_blocks: int,
) -> SyncResult:
    log = structlog.get_logger().bind(wallet_id=str(wallet.id), chain=wallet.chain)

    head = client.get_latest_block()
    ceiling = head - confirmations
    cursor = wallet.last_synced_block or 0
    from_block = cursor

    if ceiling <= cursor:
        wallet.last_synced_at = datetime.now(tz=UTC)
        session.commit()
        return SyncResult(wallet_id=wallet.id, from_block=cursor, to_block=cursor)

    totals: Counter[str] = Counter()
    chunk_start = cursor

    while chunk_start <= ceiling:
        chunk_end = min(chunk_start + chunk_blocks - 1, ceiling)

        totals += _ingest_chunk(session, client, wallet, chunk_start, chunk_end)
        session.commit()  # rows durable FIRST

        wallet.last_synced_block = chunk_end
        wallet.last_synced_at = datetime.now(tz=UTC)
        session.commit()  # cursor checkpoint SECOND

        log.info(
            "chunk_synced",
            range=f"{chunk_start}..{chunk_end}",
            transactions=totals["transactions"],
            token_transfers=totals["token_transfers"],
            internal_txs=totals["internal_txs"],
            cursor=chunk_end,
        )
        chunk_start = chunk_end + 1

    return SyncResult(
        wallet_id=wallet.id,
        from_block=from_block,
        to_block=ceiling,
        transactions=totals["transactions"],
        token_transfers=totals["token_transfers"],
        internal_txs=totals["internal_txs"],
    )
