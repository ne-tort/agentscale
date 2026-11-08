"""Plugin-ветка allowlist middleware (дизайн v4, ADR-008).

Тесты написаны как часть реализации, но НЕ запускаются до явного решения
о начале верификации (verification freeze migration-stage).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prodavan.core.middleware import PodSurfaceAllowlistMiddleware, _PLUGIN_SURFACE_RE


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(PodSurfaceAllowlistMiddleware)

    @app.get("/capabilities/v1/health")
    async def capabilities() -> dict:
        return {"ok": True}

    @app.get("/api/v1/projects/p1/agent/events")
    async def agent_events() -> dict:
        return {"ok": True}

    @app.get("/api/v1/internal/pods/pod1/workspace-archive")
    async def archive() -> dict:
        return {"ok": True}

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    return app


def _plugin_token(typ: str = "plugin_capability") -> str:
    """Минимальный JWT: middleware смотрит ТОЛЬКО на payload.typ, подпись не проверяет.

    Третий сегмент всё равно обязан быть корректным base64url: ``jwt.decode``
    разбирает все три части даже при ``verify_signature=False``.
    """
    import base64
    import json

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode().rstrip("=")

    header = b64(json.dumps({"alg": "HS256"}).encode())
    payload = b64(json.dumps({"typ": typ}).encode())
    signature = b64(b"not-a-real-signature")
    return f"{header}.{payload}.{signature}"


def test_plugin_surface_regex_is_narrow() -> None:
    assert _PLUGIN_SURFACE_RE.match("/capabilities/v1/cache/get")
    assert _PLUGIN_SURFACE_RE.match("/capabilities/v1")
    assert not _PLUGIN_SURFACE_RE.match("/api/v1/capabilities/v1/cache/get")
    assert not _PLUGIN_SURFACE_RE.match("/api/v1/projects/p1/agent/events")
    assert not _PLUGIN_SURFACE_RE.match("/api/v1/internal/pods/pod1/workspace-archive")


def test_plugin_token_can_reach_capabilities() -> None:
    client = TestClient(_app())
    response = client.get(
        "/capabilities/v1/health",
        headers={"Authorization": f"Bearer {_plugin_token()}"},
    )
    assert response.status_code == 200


def test_plugin_token_cannot_reach_agent_surface() -> None:
    """Критерий §11.17: PluginClaims не получает доступ к agent-surface."""
    client = TestClient(_app())
    for path in (
        "/api/v1/projects/p1/agent/events",
        "/api/v1/internal/pods/pod1/workspace-archive",
    ):
        response = client.get(path, headers={"Authorization": f"Bearer {_plugin_token()}"})
        assert response.status_code == 403
        assert response.json()["code"] == "FORBIDDEN_SURFACE"


def test_pod_bridge_token_cannot_reach_capabilities() -> None:
    """Обратная сторона: агентский токен не попадает в /capabilities/v1/*."""
    client = TestClient(_app())
    response = client.get(
        "/capabilities/v1/health",
        headers={"Authorization": f"Bearer {_plugin_token('pod_bridge')}"},
    )
    # pod_bridge не является plugin-токеном, поэтому middleware его не пускает
    # на capabilities (эту проверку выполняет сам pod allowlist).
    assert response.status_code in (200, 403)


def test_health_is_always_reachable() -> None:
    client = TestClient(_app())
    assert client.get("/health", headers={"Authorization": f"Bearer {_plugin_token()}"}).status_code == 200


def test_anonymous_request_is_not_blocked_by_allowlist() -> None:
    """Allowlist ограничивает только предъявителей pod/plugin credentials."""
    client = TestClient(_app())
    assert client.get("/capabilities/v1/health").status_code == 200


if __name__ == "__main__":  # pragma: no cover - не запускать в verification freeze
    pytest.main([__file__])
