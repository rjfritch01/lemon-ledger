"""sync_wallet integration tests against Testcontainers Postgres.

Session isolation: each test gets a Session joined to a savepoint so that
sync_wallet's real session.commit() calls flush to the DB but are rolled back
after the test via the outer connection-level transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator, Iterator
from typing import Any

import pytest
import sqlalchemy
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from testcontainers.postgres import PostgresContainer

from lemon_ledger.clients.exceptions import ChainWindowExceeded
from lemon_ledger.db.base import Base
from lemon_ledger.domain.chains import Chain
from lemon_ledger.ingestion.sync import sync_wallet
from lemon_ledger.models.raw import RawTransaction
from lemon_ledger.models.user import User
from lemon_ledger.models.wallet import Wallet

# ── FakeChainClient ───────────────────────────────────────────────────────────


class FakeChainClient:
    """Stub that serves canned pages without network access.

    - latest_block: returned by get_latest_block()
    - txs / transfers / internals: lists of record dicts per address
    - window_limit: if a range is wider than this many blocks, raise WindowExceeded
    """

    chain: Chain = Chain.LEMONCHAIN

    def __init__(
        self,
        latest_block: int = 1000,
        txs: list[dict[str, str]] | None = None,
        transfers: list[dict[str, str]] | None = None,
        internals: list[dict[str, str]] | None = None,
        window_limit: int | None = None,
    ) -> None:
        self._latest_block = latest_block
        self._txs = txs or []
        self._transfers = transfers or []
        self._internals = internals or []
        self._window_limit = window_limit

    def get_latest_block(self) -> int:
        return self._latest_block

    def _check_window(self, start: int, end: int) -> None:
        if self._window_limit is not None and (end - start + 1) > self._window_limit:
            raise ChainWindowExceeded(
                f"Range {start}..{end} exceeds window limit {self._window_limit}"
            )

    def get_transactions(
        self, address: str, *, start_block: int = 0, end_block: int | None = None, sort: str = "asc"
    ) -> Iterator[dict[str, str]]:
        self._check_window(start_block, end_block or self._latest_block)
        yield from self._txs

    def get_token_transfers(
        self, address: str, *, start_block: int = 0, end_block: int | None = None, sort: str = "asc"
    ) -> Iterator[dict[str, str]]:
        self._check_window(start_block, end_block or self._latest_block)
        yield from self._transfers

    def get_internal_transactions(
        self, address: str, *, start_block: int = 0, end_block: int | None = None, sort: str = "asc"
    ) -> Iterator[dict[str, str]]:
        self._check_window(start_block, end_block or self._latest_block)
        yield from self._internals

    def get_logs(
        self,
        address: str,
        *,
        from_block: int = 0,
        to_block: int | str = "latest",
        topic0: str | None = None,
    ) -> list[dict[str, str]]:
        return []

    def get_block_by_time(
        self,
        dt: object,
        closest: str = "before",
    ) -> int:
        return self._latest_block


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def sync_engine(pg_container: PostgresContainer) -> Any:
    # pg_container is the session-scoped fixture from conftest.py
    raw_url = pg_container.get_connection_url()
    if "+psycopg2" in raw_url:
        sync_url = raw_url.replace("+psycopg2", "+psycopg")
    else:
        sync_url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(sync_url, future=True)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_conn(sync_engine: Any) -> Generator[Any, None, None]:
    with sync_engine.connect() as conn:
        conn.begin()
        yield conn
        conn.rollback()


@pytest.fixture
def session(db_conn: Any) -> Session:
    return Session(bind=db_conn, join_transaction_mode="create_savepoint")


@pytest.fixture
def user(session: Session) -> User:
    u = User(id=uuid.uuid4(), clerk_user_id=f"clerk_{uuid.uuid4().hex}")
    session.add(u)
    session.flush()
    return u


@pytest.fixture
def wallet(session: Session, user: User) -> Wallet:
    w = Wallet(
        id=uuid.uuid4(),
        user_id=user.id,
        chain="lemonchain",
        address="0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        role="live",
        is_active=True,
    )
    session.add(w)
    session.flush()
    return w


def _make_tx(n: int, block: int = 100) -> dict[str, str]:
    return {
        "blockNumber": str(block),
        "hash": f"0x{'a' * 62}{n:02d}",
        "timeStamp": "1700000000",
        "value": "1000",
    }


def _make_transfer(n: int, block: int = 100) -> dict[str, str]:
    return {
        "blockNumber": str(block),
        "hash": f"0x{'b' * 62}{n:02d}",
        "timeStamp": "1700000001",
        "value": "500",
        "logIndex": str(n),
        "contractAddress": f"0x{n:040x}",  # proper 42-char hex address
    }


def _make_internal(n: int, block: int = 100) -> dict[str, str]:
    return {
        "blockNumber": str(block),
        "hash": f"0x{'c' * 62}{n:02d}",
        "timeStamp": "1700000002",
        "value": "0",
        "traceId": f"call_{n}",
    }


# ── tests ─────────────────────────────────────────────────────────────────────


def test_sync_wallet_advances_cursor(session: Session, wallet: Wallet) -> None:
    client = FakeChainClient(
        latest_block=1000,
        txs=[_make_tx(1)],
        transfers=[_make_transfer(1)],
        internals=[_make_internal(1)],
    )
    result = sync_wallet(session, client, wallet, confirmations=12, chunk_blocks=100_000)

    assert result.to_block == 1000 - 12
    session.refresh(wallet)
    assert wallet.last_synced_block == 1000 - 12
    assert wallet.last_synced_at is not None


def test_sync_wallet_row_counts(session: Session, wallet: Wallet) -> None:
    client = FakeChainClient(
        latest_block=500,
        txs=[_make_tx(1), _make_tx(2)],
        transfers=[_make_transfer(1)],
        internals=[_make_internal(1), _make_internal(2), _make_internal(3)],
    )
    result = sync_wallet(session, client, wallet, confirmations=0, chunk_blocks=100_000)

    assert result.transactions == 2
    assert result.token_transfers == 1
    assert result.internal_txs == 3


def test_sync_wallet_empty_no_rows_cursor_advances(session: Session, wallet: Wallet) -> None:
    client = FakeChainClient(latest_block=500)
    result = sync_wallet(session, client, wallet, confirmations=0, chunk_blocks=100_000)

    assert result.transactions == 0
    assert result.token_transfers == 0
    assert result.internal_txs == 0
    session.refresh(wallet)
    assert wallet.last_synced_block == 500
    assert wallet.last_synced_at is not None


def test_sync_wallet_already_at_head_noop(session: Session, wallet: Wallet) -> None:
    wallet.last_synced_block = 988
    session.flush()

    client = FakeChainClient(latest_block=1000, txs=[_make_tx(1)])
    result = sync_wallet(session, client, wallet, confirmations=12, chunk_blocks=100_000)

    # ceiling = 1000 - 12 = 988 <= cursor 988: no-op
    assert result.transactions == 0
    assert result.to_block == 988


def test_sync_wallet_idempotent(session: Session, wallet: Wallet) -> None:
    """Running sync twice yields the same row count — no duplicates."""
    client = FakeChainClient(
        latest_block=500,
        txs=[_make_tx(1), _make_tx(2)],
        transfers=[_make_transfer(1)],
        internals=[_make_internal(1)],
    )
    r1 = sync_wallet(session, client, wallet, confirmations=0, chunk_blocks=100_000)
    r2 = sync_wallet(session, client, wallet, confirmations=0, chunk_blocks=100_000)

    # Second run starts with cursor at 500 → ceiling=500, no-op
    assert r1.transactions == 2
    assert r2.transactions == 0

    # Verify no duplicate rows in DB
    tx_count = session.scalar(
        select(sqlalchemy.func.count())
        .select_from(RawTransaction)
        .where(RawTransaction.wallet_id == wallet.id)
    )
    assert tx_count == 2


def test_sync_wallet_window_subdivision(session: Session, wallet: Wallet) -> None:
    """When the client raises WindowExceeded, the range is halved and retried."""
    client = FakeChainClient(
        latest_block=200,
        txs=[_make_tx(1, block=100)],
        transfers=[],
        internals=[],
        window_limit=50,  # any range > 50 blocks triggers WindowExceeded
    )
    # chunk_blocks=200 > window_limit=50, so subdivision must happen
    result = sync_wallet(session, client, wallet, confirmations=0, chunk_blocks=200)
    # At least some transactions should have been ingested
    assert result.transactions >= 1
