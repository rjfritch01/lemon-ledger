import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lemon_ledger.db.engine import get_engine

_runner: asyncio.Runner | None = None


def _get_runner() -> asyncio.Runner:
    global _runner
    if _runner is None:
        _runner = asyncio.Runner()
    return _runner


def _sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency — yields an AsyncSession; caller owns the transaction."""
    async with _sessionmaker()() as session:
        yield session


def run_async[T](coro_factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """Execute an async coroutine from sync (Celery) context.

    Uses a persistent asyncio.Runner per worker process so the asyncpg
    engine binds to a single event loop and pools connections normally.
    """
    return _get_runner().run(coro_factory())
