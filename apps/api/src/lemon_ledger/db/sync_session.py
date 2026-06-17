from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from lemon_ledger.config import Settings


def build_sync_engine(settings: Settings) -> Engine:
    url = settings.database_url
    if "+asyncpg" in url:
        url = url.replace("+asyncpg", "+psycopg")
    elif url.startswith("postgresql://") and "+" not in url.split("://")[0]:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(
        url,
        pool_size=settings.worker_db_pool_size,
        max_overflow=settings.worker_db_max_overflow,
        pool_pre_ping=True,
        future=True,
    )


def build_sync_sessionmaker(engine: Engine) -> "sessionmaker[Session]":
    return sessionmaker(engine, expire_on_commit=False)


@contextmanager
def worker_session(maker: "sessionmaker[Session]") -> Generator[Session, None, None]:
    """Yield a Session; roll back and close on any exception.

    Deliberately does NOT commit — sync_wallet owns its own commit cadence.
    """
    session = maker()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
