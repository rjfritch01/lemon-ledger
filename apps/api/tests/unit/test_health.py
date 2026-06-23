from fastapi.testclient import TestClient

from lemon_ledger.api.app import create_app

_client = TestClient(create_app())


def test_health_returns_ok() -> None:
    response = _client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_returns_ready() -> None:
    response = _client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
