"""Unit tests — /health/ready required infra gates."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prodavan.api.v1 import health as health_mod


def _app_with_ready() -> FastAPI:
    app = FastAPI()
    app.include_router(health_mod.router, prefix="/api/v1")
    return app


@pytest.fixture
def ready_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(health_mod.settings, "kafka_required", False)
    monkeypatch.setattr(health_mod.settings, "object_store_required", False)
    monkeypatch.setattr(health_mod.settings, "celery_required", False)
    monkeypatch.setattr(health_mod.settings, "redis_required", False)

    engine = MagicMock()
    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    engine.connect = MagicMock(return_value=conn)
    monkeypatch.setattr(health_mod, "get_engine", lambda: engine)
    monkeypatch.setattr(health_mod, "get_redis_manager", lambda: None)
    monkeypatch.setattr(health_mod, "get_lifespan_manager", lambda: None)
    return TestClient(_app_with_ready())


def test_ready_ok_without_required(ready_client: TestClient) -> None:
    resp = ready_client.get("/api/v1/health/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["checks"]["database"] == "ok"


def test_ready_fails_when_kafka_required(monkeypatch: pytest.MonkeyPatch, ready_client: TestClient) -> None:
    monkeypatch.setattr(health_mod.settings, "kafka_required", True)

    class _Life:
        async def health_report(self) -> dict:
            return {"kafka": False, "file_store": True, "worker": None}

    monkeypatch.setattr(health_mod, "get_lifespan_manager", lambda: _Life())
    # Attach lifespan via app.state for request path
    app = ready_client.app
    app.state.lifespan_manager = _Life()

    resp = ready_client.get("/api/v1/health/ready")
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["code"] == "NOT_READY"
    assert "kafka" in detail["message"]


def test_ready_ok_when_kafka_required_and_healthy(
    monkeypatch: pytest.MonkeyPatch, ready_client: TestClient
) -> None:
    monkeypatch.setattr(health_mod.settings, "kafka_required", True)
    monkeypatch.setattr(health_mod.settings, "object_store_required", True)

    class _Life:
        async def health_report(self) -> dict:
            return {"kafka": True, "file_store": True, "worker": True}

    ready_client.app.state.lifespan_manager = _Life()
    monkeypatch.setattr(health_mod, "get_lifespan_manager", lambda: _Life())

    resp = ready_client.get("/api/v1/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["checks"]["kafka"] == "ok"
    assert body["checks"]["file_store"] == "ok"

def test_ready_sandbox_mode_requires_sandbox_client_not_k8s(
    monkeypatch: pytest.MonkeyPatch, ready_client: TestClient
) -> None:
    """pod_runtime_mode=sandbox: k8s gate is mode-gated off; sandbox resource gates."""
    monkeypatch.setattr(health_mod.settings, "pod_runtime_mode", "sandbox")
    monkeypatch.setattr(health_mod.settings, "pod_k8s_required", True)

    class _Life:
        async def health_report(self) -> dict:
            return {"k8s": None, "sandbox": None, "file_store": True}

    ready_client.app.state.lifespan_manager = _Life()
    resp = ready_client.get("/api/v1/health/ready")
    assert resp.status_code == 503
    assert "sandbox" in resp.json()["detail"]["message"]


def test_ready_sandbox_mode_ok_when_sandbox_client_healthy(
    monkeypatch: pytest.MonkeyPatch, ready_client: TestClient
) -> None:
    monkeypatch.setattr(health_mod.settings, "pod_runtime_mode", "sandbox")
    monkeypatch.setattr(health_mod.settings, "pod_k8s_required", True)

    class _Life:
        async def health_report(self) -> dict:
            return {"k8s": None, "sandbox": True, "file_store": True}

    ready_client.app.state.lifespan_manager = _Life()
    resp = ready_client.get("/api/v1/health/ready")
    assert resp.status_code == 200
    assert resp.json()["checks"]["sandbox"] == "ok"


def test_ready_k8s_mode_still_requires_k8s(
    monkeypatch: pytest.MonkeyPatch, ready_client: TestClient
) -> None:
    monkeypatch.setattr(health_mod.settings, "pod_runtime_mode", "k8s")
    monkeypatch.setattr(health_mod.settings, "pod_k8s_required", True)

    class _Life:
        async def health_report(self) -> dict:
            return {"k8s": False, "sandbox": None, "file_store": True}

    ready_client.app.state.lifespan_manager = _Life()
    resp = ready_client.get("/api/v1/health/ready")
    assert resp.status_code == 503
    assert "k8s" in resp.json()["detail"]["message"]
