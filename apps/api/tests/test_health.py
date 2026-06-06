from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from lemon_ledger.db.session import get_session
from lemon_ledger.main import app


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_liveness(client: AsyncClient) -> None:
    resp = await client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_readiness_healthy(client: AsyncClient) -> None:
    with patch("lemon_ledger.api.health.Redis") as mock_redis_cls:
        mock_r = AsyncMock()
        mock_r.ping = AsyncMock(return_value=True)
        mock_r.__aenter__ = AsyncMock(return_value=mock_r)
        mock_r.__aexit__ = AsyncMock(return_value=False)
        mock_redis_cls.from_url.return_value = mock_r
        resp = await client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["db"] == "healthy"
    assert body["redis"] == "healthy"


async def test_readiness_redis_down(client: AsyncClient) -> None:
    with patch("lemon_ledger.api.health.Redis") as mock_redis_cls:
        mock_r = AsyncMock()
        mock_r.ping = AsyncMock(side_effect=ConnectionError("redis down"))
        mock_r.__aenter__ = AsyncMock(return_value=mock_r)
        mock_r.__aexit__ = AsyncMock(return_value=False)
        mock_redis_cls.from_url.return_value = mock_r
        resp = await client.get("/health/ready")
    assert resp.status_code == 503
    assert "redis" in resp.json()["detail"]
