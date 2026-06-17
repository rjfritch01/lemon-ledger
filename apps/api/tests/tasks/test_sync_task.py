"""Task-level tests.

Lock-skip and not-found use a real Redis (Testcontainers); the happy-path
uses _run_sync with a client_factory injection so no network calls are made.
All three tests require Docker.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator, Iterator
from typing import Any
from unittest.mock import patch

import pytest
import redis as redis_lib
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from lemon_ledger.clients.base import ChainClient
from lemon_ledger.config import Settings
from lemon_ledger.db.base import Base
from lemon_ledger.domain.chains import Chain
from lemon_ledger.models.user import User
from lemon_ledger.models.wallet import Wallet
from lemon_ledger.tasks.sync import _run_sync, sync_wallet_task, wallet_sync_lock
from lemon_ledger.worker import Resources


class _FakeChainClient:
    chain: Chain = Chain.LEMONCHAIN

    def __init__(self) -> None:
        self._latest_block = 500

    def get_latest_block(self) -> int:
        return self._latest_block

    def get_transactions(self, address: str, **kw: Any) -> Iterator[dict[str, str]]:
        return iter([])

    def get_token_transfers(self, address: str, **kw: Any) -> Iterator[dict[str, str]]:
        return iter([])

    def get_internal_transactions(self, address: str, **kw: Any) -> Iterator[dict[str, str]]:
        return iter([])

    def get_logs(self, address: str, **kw: Any) -> list[dict[str, str]]:
        return []


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def redis_container() -> Generator[RedisContainer, None, None]:
    with RedisContainer("redis:7-alpine") as container:
        yield container


@pytest.fixture
def redis_client(redis_container: RedisContainer) -> Any:
    host = redis_container.get_container_host_ip()
    port = int(redis_container.get_exposed_port(6379))
    r = redis_lib.Redis(host=host, port=port, db=0)
    r.flushdb()
    return r


@pytest.fixture(scope="module")
def task_engine(pg_container: PostgresContainer) -> Any:
    # pg_container from conftest.py (session scope)
    raw_url = pg_container.get_connection_url()
    if "+psycopg2" in raw_url:
        sync_url = raw_url.replace("+psycopg2", "+psycopg")
    else:
        sync_url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(sync_url, future=True)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture(scope="module")
def task_maker(task_engine: Any) -> sessionmaker[Session]:
    return sessionmaker(task_engine, expire_on_commit=False)


@pytest.fixture
def seeded_wallet(task_maker: sessionmaker[Session]) -> Generator[Wallet, None, None]:
    with task_maker() as session:
        user = User(id=uuid.uuid4(), clerk_user_id=f"clerk_{uuid.uuid4().hex}")
        session.add(user)
        w = Wallet(
            id=uuid.uuid4(),
            user_id=user.id,
            chain="lemonchain",
            address=f"0x{'a' * 40}",
            role="live",
            is_active=True,
        )
        session.add(w)
        session.commit()
    yield w
    with task_maker() as session:
        session.delete(session.get(Wallet, w.id))
        session.commit()


def _make_resources(engine: Any, maker: Any, redis: Any) -> Resources:
    import httpx

    return Resources(
        engine=engine,
        sessionmaker=maker,
        redis=redis,
        http=httpx.Client(),
    )


# ── wallet_sync_lock tests ────────────────────────────────────────────────────


def test_lock_skip_returns_skipped(redis_client: Any) -> None:
    wallet_id = str(uuid.uuid4())
    # Pre-set the lock key to simulate another worker holding it
    redis_client.set(f"lock:sync:{wallet_id}", "other-token", ex=60)

    with wallet_sync_lock(redis_client, wallet_id, ttl_s=60) as acquired:
        assert not acquired


def test_lock_acquired_then_released(redis_client: Any) -> None:
    wallet_id = str(uuid.uuid4())
    with wallet_sync_lock(redis_client, wallet_id, ttl_s=60) as acquired:
        assert acquired
        assert redis_client.exists(f"lock:sync:{wallet_id}")
    # After context exit the key should be gone
    assert not redis_client.exists(f"lock:sync:{wallet_id}")


# ── _run_sync unit tests ──────────────────────────────────────────────────────


def test_run_sync_happy_path(
    seeded_wallet: Wallet,
    task_maker: sessionmaker[Session],
    redis_client: Any,
    task_engine: Any,
) -> None:
    """_run_sync with injected client_factory — no network, no patch required."""
    res = _make_resources(task_engine, task_maker, redis_client)
    settings = Settings()

    def fake_factory(chain: Chain, r: Resources, s: Settings) -> ChainClient:
        return _FakeChainClient()

    result = _run_sync(str(seeded_wallet.id), None, res, settings, client_factory=fake_factory)
    assert result.wallet_id == seeded_wallet.id
    assert "transactions" in result.__dataclass_fields__


def test_run_sync_not_found(
    task_maker: sessionmaker[Session],
    redis_client: Any,
    task_engine: Any,
) -> None:
    res = _make_resources(task_engine, task_maker, redis_client)
    settings = Settings()
    bad_id = str(uuid.uuid4())

    def fake_factory(chain: Chain, r: Resources, s: Settings) -> ChainClient:
        return _FakeChainClient()

    with pytest.raises(ValueError, match="not found or inactive"):
        _run_sync(bad_id, None, res, settings, client_factory=fake_factory)


# ── sync_wallet_task tests ────────────────────────────────────────────────────


def test_sync_wallet_task_not_found(
    task_maker: sessionmaker[Session], redis_client: Any, task_engine: Any
) -> None:
    bad_id = str(uuid.uuid4())
    fake_resources = _make_resources(task_engine, task_maker, redis_client)

    with (
        patch("lemon_ledger.tasks.sync.resources") as mock_res,
        patch("lemon_ledger.tasks.sync.get_settings", return_value=Settings()),
        patch("lemon_ledger.tasks.sync.build_chain_client", return_value=_FakeChainClient()),
    ):
        mock_res.ensure.return_value = fake_resources
        with pytest.raises(ValueError, match="not found or inactive"):
            sync_wallet_task.apply(args=[bad_id]).get(propagate=True)


def test_sync_wallet_task_happy_path(
    seeded_wallet: Wallet,
    task_maker: sessionmaker[Session],
    redis_client: Any,
    task_engine: Any,
) -> None:
    """Happy-path: inject client_factory via build_chain_client patch."""
    fake_resources = _make_resources(task_engine, task_maker, redis_client)
    settings = Settings()

    with (
        patch("lemon_ledger.tasks.sync.resources") as mock_res,
        patch("lemon_ledger.tasks.sync.get_settings", return_value=settings),
        patch("lemon_ledger.tasks.sync.build_chain_client", return_value=_FakeChainClient()),
    ):
        mock_res.ensure.return_value = fake_resources
        result = sync_wallet_task.apply(args=[str(seeded_wallet.id)]).get(propagate=True)

    assert result["wallet_id"] == str(seeded_wallet.id)
    assert "transactions" in result
