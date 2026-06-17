"""Migration integration tests for the four raw ingestion tables.

Verifies schema correctness via Testcontainers Postgres after running
alembic upgrade head, then confirms downgrade reverses all changes.
"""

from collections.abc import AsyncGenerator, Generator, Sequence

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def raw_pg_container() -> Generator[PostgresContainer, None, None]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="module")
def raw_db_url(raw_pg_container: PostgresContainer) -> str:
    raw: str = raw_pg_container.get_connection_url()
    if "+psycopg2" in raw:
        return raw.replace("+psycopg2", "+asyncpg")
    return raw.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture(scope="module", autouse=True)
def apply_all_migrations(raw_db_url: str) -> None:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", raw_db_url)
    command.upgrade(cfg, "head")


# ── helpers ───────────────────────────────────────────────────────────────────


async def _scalar(session: AsyncSession, sql: str, params: dict[str, str]) -> str | None:
    result = await session.execute(text(sql), params)
    row = result.fetchone()
    return str(row[0]) if row else None


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.fixture
async def db(raw_db_url: str) -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(raw_db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


_RAW_TABLES = (
    "raw_transactions",
    "raw_token_transfers",
    "raw_internal_txs",
    "raw_logs",
)


@pytest.mark.parametrize("table", _RAW_TABLES)
async def test_table_exists(db: AsyncSession, table: str) -> None:
    name = await _scalar(
        db,
        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename=:t",
        {"t": table},
    )
    assert name == table, f"Table {table!r} was not created"


async def test_raw_columns_are_jsonb(db: AsyncSession) -> None:
    for table in _RAW_TABLES:
        udt = await _scalar(
            db,
            "SELECT udt_name FROM information_schema.columns "
            "WHERE table_name=:t AND column_name='raw'",
            {"t": table},
        )
        assert udt == "jsonb", f"{table}.raw should be jsonb, got {udt!r}"


async def test_chain_columns_are_string_not_enum(db: AsyncSession) -> None:
    for table in _RAW_TABLES:
        dtype = await _scalar(
            db,
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name=:t AND column_name='chain'",
            {"t": table},
        )
        assert dtype == "character varying", (
            f"{table}.chain should be varchar (String), got {dtype!r} — no PG enum allowed"
        )


async def test_chain_check_constraints_exist(db: AsyncSession) -> None:
    for table in _RAW_TABLES:
        short = table.replace("raw_", "")
        con_name = f"ck_raw_{short}_chain" if short else f"ck_{table}_chain"
        # use actual pattern: ck_<tablename>_chain
        con_name = f"ck_{table}_chain"
        row = await _scalar(
            db,
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_name=:t AND constraint_type='CHECK' AND constraint_name=:c",
            {"t": table, "c": con_name},
        )
        assert row == con_name, f"Missing CHECK constraint {con_name!r} on {table}"


async def test_value_columns_are_numeric(db: AsyncSession) -> None:
    for table in ("raw_transactions", "raw_token_transfers", "raw_internal_txs"):
        dtype = await _scalar(
            db,
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name=:t AND column_name='value'",
            {"t": table},
        )
        assert dtype == "numeric", f"{table}.value should be numeric, got {dtype!r}"


async def test_unique_constraints_exist(db: AsyncSession) -> None:
    expected: Sequence[tuple[str, str]] = [
        ("raw_transactions", "uq_raw_transactions_wallet_tx"),
        ("raw_token_transfers", "uq_raw_token_transfers_wallet_tx_log"),
        ("raw_internal_txs", "uq_raw_internal_txs_wallet_tx_trace"),
        ("raw_logs", "uq_raw_logs_wallet_tx_log"),
    ]
    for table, con_name in expected:
        row = await _scalar(
            db,
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_name=:t AND constraint_type='UNIQUE' AND constraint_name=:c",
            {"t": table, "c": con_name},
        )
        assert row == con_name, f"Missing UNIQUE constraint {con_name!r} on {table}"


async def test_wallet_block_indexes_exist(db: AsyncSession) -> None:
    for table in _RAW_TABLES:
        idx = f"ix_{table}_wallet_block"
        row = await _scalar(
            db,
            "SELECT indexname FROM pg_indexes WHERE tablename=:t AND indexname=:i",
            {"t": table, "i": idx},
        )
        assert row == idx, f"Missing composite index {idx!r} on {table}"


async def test_fk_to_wallets_exists(db: AsyncSession) -> None:
    for table in _RAW_TABLES:
        row = await _scalar(
            db,
            "SELECT rc.constraint_name FROM information_schema.referential_constraints rc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON rc.constraint_name = kcu.constraint_name "
            "WHERE kcu.table_name=:t AND kcu.column_name='wallet_id'",
            {"t": table},
        )
        assert row is not None, f"Missing FK wallet_id → wallets.id on {table}"


async def test_downgrade_drops_raw_tables(raw_db_url: str) -> None:
    import asyncio

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", raw_db_url)

    # Alembic's env.py calls asyncio.run() internally; run it in a thread to
    # avoid "cannot be called from a running event loop" from pytest-asyncio.
    # Target the revision just before raw tables (294f76baacc3 = initial_schema)
    # so that any migrations layered on top don't affect this test.
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: command.downgrade(cfg, "294f76baacc3"))

    engine = create_async_engine(raw_db_url)
    async with engine.connect() as conn:
        for table in _RAW_TABLES:
            result = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename=:t"),
                {"t": table},
            )
            assert result.fetchone() is None, f"Table {table!r} still exists after downgrade"
    await engine.dispose()

    # Re-apply so subsequent tests in the session are unaffected
    await loop.run_in_executor(None, lambda: command.upgrade(cfg, "head"))
