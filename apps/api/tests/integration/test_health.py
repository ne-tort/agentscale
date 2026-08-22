"""Integration tests — L00 health + error envelope."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from prodavan.domain.errors import AppError
from prodavan.main import create_app


def test_health_check_returns_ok_with_build_meta() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "build" in body
    assert "service" in body


def test_liveness_at_root_and_v1() -> None:
    client = TestClient(create_app())
    for path in ("/health/live", "/api/v1/health/live"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == {"status": "alive"}


def test_root_health_ok_with_meta() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]


def test_stub_marker() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/stub")
    assert response.status_code == 200
    assert response.json()["status"] == "stub"


def test_app_error_problem_json() -> None:
    app: FastAPI = create_app()

    @app.get("/_test/app-error")
    async def _boom() -> None:
        raise AppError(
            code="TEST_ERROR",
            title="Test Error",
            status=409,
            detail="conflict for test",
        )

    client = TestClient(app)
    response = client.get("/_test/app-error")
    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "TEST_ERROR"
    assert body["status"] == 409
    assert body["detail"] == "conflict for test"


def test_not_found_problem_json() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/no-such-route")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "NOT_FOUND"
