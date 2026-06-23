from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from lemon_ledger.core.config import get_settings

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    """Return the process-global async engine, creating it on first call."""
    global _engine
    if _engine is None:
        s = get_settings()
        _engine = create_async_engine(
            str(s.DATABASE_URL),
            pool_size=s.DB_POOL_SIZE,
            max_overflow=s.DB_MAX_OVERFLOW,
            pool_recycle=s.DB_POOL_RECYCLE_SECONDS,
            pool_pre_ping=True,
            echo=s.DB_ECHO,
        )
    return _engine


async def dispose_engine() -> None:
    """Dispose the global engine and reset it; useful for clean shutdown/tests."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
