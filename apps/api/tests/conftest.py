"""Shared pytest fixtures.

DB testing convention (reused by every later PR):
- Session-scoped Testcontainers Postgres starts once and sets DATABASE_URL.
- Function-scoped db_session runs each test inside a savepoint that is rolled
  back on teardown, keeping tests isolated without hitting the filesystem.
"""

import os

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session", autouse=True)
def postgres_container() -> str:  # type: ignore[return]
    with PostgresContainer("postgres:16-alpine") as pg:
        raw_url: str = pg.get_connection_url()
        # testcontainers returns a psycopg2 URL; swap driver to asyncpg.
        async_url = raw_url.replace("+psycopg2", "+asyncpg").replace(
            "postgresql://", "postgresql+asyncpg://", 1
        )
        os.environ["DATABASE_URL"] = async_url

        # Clear settings cache so all test code picks up the testcontainer URL.
        from lemon_ledger.core.config import get_settings

        get_settings.cache_clear()

        # Apply all migrations so models resolve against a current schema.
        from alembic import command
        from alembic.config import Config

        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", async_url)
        command.upgrade(cfg, "head")

        yield async_url


@pytest.fixture
async def db_session(postgres_container: str) -> AsyncSession:  # type: ignore[return]
    """Function-scoped AsyncSession using the savepoint isolation pattern.

    Opens a connection, begins an outer transaction, then wraps each test in
    a SAVEPOINT.  Teardown rolls back to the savepoint then the outer
    transaction, leaving the schema in exactly the state it was before the test.
    """
    engine = create_async_engine(postgres_container)
    async with engine.connect() as conn:
        await conn.begin()
        session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
        yield session
        await session.close()
        await conn.rollback()
    await engine.dispose()
