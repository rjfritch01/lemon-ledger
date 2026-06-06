from collections.abc import AsyncGenerator, Generator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session")
def pg_container() -> Generator[PostgresContainer, None, None]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def test_db_url(pg_container: PostgresContainer) -> str:
    raw: str = pg_container.get_connection_url()
    if "+psycopg2" in raw:
        return raw.replace("+psycopg2", "+asyncpg")
    return raw.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture(scope="session", autouse=True)
def apply_migrations(test_db_url: str) -> None:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", test_db_url)
    command.upgrade(cfg, "head")


@pytest.fixture
async def db_session(test_db_url: str) -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(test_db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()
