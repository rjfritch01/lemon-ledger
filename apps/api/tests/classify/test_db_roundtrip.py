"""Testcontainers-backed roundtrip for the classify layer.

Exercises against the real migrated schema (same pg_container as the rest of
the test suite — session-scoped from tests/conftest.py):

  - CHECK constraint on classification
  - CHECK constraint on chain
  - UNIQUE (wallet_id, tx_hash, event_seq) respected by replace_classified
  - ARRAY(UUID) column accepts both NULL and a real list
  - replace_classified DELETE + add_all under savepoint transaction semantics

Not marked @integration — Docker is required but this runs in the default gate
alongside the other Testcontainers tests (test_sync.py, test_sync_task.py).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
import sqlalchemy.exc
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.postgres import PostgresContainer

from lemon_ledger.classify.tasks import _run_classify
from lemon_ledger.db.base import Base
from lemon_ledger.models.classified import ClassifiedTransaction
from lemon_ledger.models.raw import RawTokenTransfer
from lemon_ledger.models.user import User
from lemon_ledger.models.wallet import Wallet

log = logging.getLogger(__name__)

WALLET_ADDR = "0x" + "a" * 40
SENDER_ADDR = "0x" + "b" * 40
CONTRACT_ADDR = "0x" + "c" * 40
TX_HASH = "0x" + "d" * 64
BLOCK = 50


# ── module-scoped engine (reuses the session-scoped pg_container) ─────────────


@pytest.fixture(scope="module")
def classify_engine(pg_container: PostgresContainer) -> Any:
    raw_url = pg_container.get_connection_url()
    if "+psycopg2" in raw_url:
        sync_url = raw_url.replace("+psycopg2", "+psycopg")
    else:
        sync_url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(sync_url, future=True)
    # Schema is already at head via apply_migrations autouse in root conftest.
    # create_all is a no-op here (checkfirst=True by default) but keeps the
    # fixture self-contained if run in isolation.
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture(scope="module")
def classify_maker(classify_engine: Any) -> sessionmaker[Session]:
    return sessionmaker(classify_engine, expire_on_commit=False)


# ── function-scoped savepoint isolation ───────────────────────────────────────


@pytest.fixture
def db_conn(classify_engine: Any) -> Generator[Any, None, None]:
    with classify_engine.connect() as conn:
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
        address=WALLET_ADDR,
        role="live",
        is_active=True,
        last_synced_block=BLOCK,
        last_classified_block=None,
    )
    session.add(w)
    session.flush()
    return w


def _seed_transfer(session: Session, wallet: Wallet, *, log_index: int = 0) -> RawTokenTransfer:
    """Insert one ERC-20 transfer-in directed at the wallet address."""
    t = RawTokenTransfer(
        id=uuid.uuid4(),
        wallet_id=wallet.id,
        chain="lemonchain",
        block_number=BLOCK,
        tx_hash=TX_HASH,
        occurred_at=datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC),
        raw={
            "from": SENDER_ADDR,
            "to": WALLET_ADDR,
            "value": "1000000000000000000",
            "tokenDecimal": "18",
        },
        value=Decimal("1000000000000000000"),
        log_index=log_index,
        contract_address=CONTRACT_ADDR,
    )
    session.add(t)
    session.flush()
    return t


def _mock_pricing() -> MagicMock:
    p = MagicMock()
    p.get_historical_price.return_value = None
    return p


# ── tests ─────────────────────────────────────────────────────────────────────


def test_classify_persists_transfer_in(session: Session, wallet: Wallet) -> None:
    """_run_classify writes a transfer-in row that satisfies all DB constraints."""
    _seed_transfer(session, wallet)

    result = _run_classify(str(wallet.id), _mock_pricing(), session, logging.getLogger("test"))

    assert result["classified"] == 1
    assert result["from_block"] == 1
    assert result["to_block"] == BLOCK

    rows = session.scalars(
        select(ClassifiedTransaction).where(ClassifiedTransaction.wallet_id == wallet.id)
    ).all()
    assert len(rows) == 1

    row = rows[0]
    assert row.classification == "transfer-in"
    assert row.chain == "lemonchain"
    assert row.tx_hash == TX_HASH
    assert row.event_seq == 0
    assert row.amount == Decimal("1")  # 1e18 wei / 1e18
    assert row.block_number == BLOCK
    assert row.needs_review is False
    assert row.manual_override is False
    assert row.related_lots is None  # ARRAY(UUID) column: NULL accepted
    assert row.bridge_correlation_id is None

    # Cursor was advanced
    refreshed = session.get(Wallet, wallet.id)
    assert refreshed is not None
    assert refreshed.last_classified_block == BLOCK


def test_replace_classified_no_duplicate_on_rerun(session: Session, wallet: Wallet) -> None:
    """Running classify twice over the same range replaces rows, not appends."""
    _seed_transfer(session, wallet)

    _run_classify(str(wallet.id), _mock_pricing(), session, logging.getLogger("test"))

    # Reset cursor so the second run covers the same range again.
    w = session.get(Wallet, wallet.id)
    assert w is not None
    w.last_classified_block = None
    w.last_synced_block = BLOCK
    session.flush()

    _run_classify(str(wallet.id), _mock_pricing(), session, logging.getLogger("test"))

    rows = session.scalars(
        select(ClassifiedTransaction).where(ClassifiedTransaction.wallet_id == wallet.id)
    ).all()
    # Still exactly one row; replace_classified deleted the old one before inserting.
    assert len(rows) == 1
    assert rows[0].event_seq == 0


def test_array_uuid_column_accepts_value(session: Session, wallet: Wallet) -> None:
    """ARRAY(UUID) column round-trips a real list of UUIDs."""
    lot_id = uuid.uuid4()
    row = ClassifiedTransaction(
        id=uuid.uuid4(),
        wallet_id=wallet.id,
        chain="lemonchain",
        tx_hash="0x" + "e" * 64,
        event_seq=0,
        block_number=1,
        occurred_at=datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC),
        classification="transfer-in",
        contract_address=CONTRACT_ADDR,
        amount=Decimal("1"),
        related_lots=[lot_id],  # non-NULL ARRAY value
    )
    session.add(row)
    session.flush()

    fetched = session.get(ClassifiedTransaction, row.id)
    assert fetched is not None
    assert fetched.related_lots == [lot_id]


def test_unique_wallet_tx_seq_enforced(session: Session, wallet: Wallet) -> None:
    """Inserting a duplicate (wallet_id, tx_hash, event_seq) raises IntegrityError."""
    shared_kwargs = dict(
        wallet_id=wallet.id,
        chain="lemonchain",
        tx_hash="0x" + "f" * 64,
        event_seq=0,
        block_number=1,
        occurred_at=datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC),
        classification="transfer-in",
        contract_address=CONTRACT_ADDR,
        amount=Decimal("1"),
    )
    session.add(ClassifiedTransaction(id=uuid.uuid4(), **shared_kwargs))
    session.flush()

    session.add(ClassifiedTransaction(id=uuid.uuid4(), **shared_kwargs))  # same seq
    with pytest.raises(sqlalchemy.exc.IntegrityError, match="uq_classified_wallet_tx_seq"):
        session.flush()
    session.rollback()


def test_check_constraint_rejects_bad_classification(session: Session, wallet: Wallet) -> None:
    """classification column CHECK fires on an unrecognised value."""
    row = ClassifiedTransaction(
        id=uuid.uuid4(),
        wallet_id=wallet.id,
        chain="lemonchain",
        tx_hash="0x" + "9" * 64,
        event_seq=0,
        block_number=1,
        occurred_at=datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC),
        classification="totally-wrong",  # not in ClassificationKind
        contract_address=CONTRACT_ADDR,
        amount=Decimal("1"),
    )
    session.add(row)
    with pytest.raises(sqlalchemy.exc.IntegrityError, match="ck_classification_kind"):
        session.flush()
    session.rollback()


def test_check_constraint_rejects_bad_chain(session: Session, wallet: Wallet) -> None:
    """chain column CHECK fires on an unrecognised chain string."""
    row = ClassifiedTransaction(
        id=uuid.uuid4(),
        wallet_id=wallet.id,
        chain="ethereum",  # not in the CHAIN_SQL allowlist
        tx_hash="0x" + "8" * 64,
        event_seq=0,
        block_number=1,
        occurred_at=datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC),
        classification="transfer-in",
        contract_address=CONTRACT_ADDR,
        amount=Decimal("1"),
    )
    session.add(row)
    with pytest.raises(sqlalchemy.exc.IntegrityError, match="ck_classified_chain"):
        session.flush()
    session.rollback()
