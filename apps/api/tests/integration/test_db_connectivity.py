"""DB connectivity — establishes the testing convention for later PRs."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


async def test_engine_connects_and_select_1(postgres_container: str) -> None:
    engine = create_async_engine(postgres_container)
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        assert result.scalar() == 1
    await engine.dispose()


async def test_session_begin_and_rollback(db_session: AsyncSession) -> None:
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1


async def test_run_async_executes_coroutine() -> None:
    from lemon_ledger.db.session import run_async

    async def _trivial() -> int:
        return 42

    value = run_async(_trivial)
    assert value == 42
