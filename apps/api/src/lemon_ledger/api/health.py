from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lemon_ledger.config import get_settings
from lemon_ledger.db.session import get_session

router = APIRouter(prefix="/health", tags=["health"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def readiness(session: SessionDep) -> dict[str, str]:
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database_unhealthy") from exc

    try:
        settings = get_settings()
        async with Redis.from_url(settings.redis_url) as r:
            await r.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="redis_unhealthy") from exc

    return {"status": "ok", "db": "healthy", "redis": "healthy"}
