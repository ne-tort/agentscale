"""Integration tests."""

from fastapi.testclient import TestClient

from prodavan.main import create_app


def test_health_check_returns_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_liveness_at_root_and_v1() -> None:
    client = TestClient(create_app())
    for path in ("/health/live", "/api/v1/health/live"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == {"status": "alive"}


def test_root_health_ok() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
