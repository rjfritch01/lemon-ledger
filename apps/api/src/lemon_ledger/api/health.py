from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.responses import Response

from lemon_ledger.db.engine import get_engine

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — always 200 while the process is up."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready() -> Response:
    """Readiness probe — 200 when Postgres is reachable, 503 otherwise."""
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return JSONResponse({"status": "ready"})
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
