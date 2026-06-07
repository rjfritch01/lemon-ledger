"""Task-level tests.

Lock-skip and not-found use a real Redis (Testcontainers); the happy-path
monkeypatches build_blockscout_client to inject a FakeBlockscoutClient so
no network calls are made.  All three tests require Docker.
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

from lemon_ledger.db.base import Base
from lemon_ledger.models.user import User
from lemon_ledger.models.wallet import Wallet
from lemon_ledger.tasks.sync import sync_wallet_task, wallet_sync_lock

# ── FakeBlockscoutClient (imported from ingestion tests via shared helper) ─────


class _FakeClient:
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


# ── sync_wallet_task tests ────────────────────────────────────────────────────


def test_sync_wallet_task_not_found(
    task_maker: sessionmaker[Session], redis_client: Any, task_engine: Any
) -> None:
    bad_id = str(uuid.uuid4())
    from lemon_ledger.worker import Resources

    fake_resources = Resources(
        engine=task_engine,
        sessionmaker=task_maker,
        redis=redis_client,
        http=__import__("httpx").Client(),
    )

    from lemon_ledger.config import Settings

    with (
        patch("lemon_ledger.tasks.sync.resources") as mock_res,
        patch("lemon_ledger.tasks.sync.get_settings", return_value=Settings()),
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
    """Happy-path: monkeypatch build_blockscout_client to avoid network."""
    from lemon_ledger.config import Settings
    from lemon_ledger.worker import Resources

    fake_resources = Resources(
        engine=task_engine,
        sessionmaker=task_maker,
        redis=redis_client,
        http=__import__("httpx").Client(),
    )

    settings = Settings()

    with (
        patch("lemon_ledger.tasks.sync.resources") as mock_res,
        patch("lemon_ledger.tasks.sync.get_settings", return_value=settings),
        patch("lemon_ledger.tasks.sync.build_blockscout_client", return_value=_FakeClient()),
    ):
        mock_res.ensure.return_value = fake_resources
        result = sync_wallet_task.apply(args=[str(seeded_wallet.id)]).get(propagate=True)

    assert result["wallet_id"] == str(seeded_wallet.id)
    assert "transactions" in result
