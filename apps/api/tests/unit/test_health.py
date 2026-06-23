from fastapi.testclient import TestClient

from lemon_ledger.api.app import create_app


def test_health_returns_ok() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_returns_200_when_db_is_up(postgres_container: str) -> None:
    """Uses the testcontainer fixture (autouse, session-scoped) to ensure
    DATABASE_URL is set before the engine is lazily created."""
    import asyncio

    from lemon_ledger.db.engine import dispose_engine

    asyncio.run(dispose_engine())  # reset any engine bound to a previous loop
    with TestClient(create_app()) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
