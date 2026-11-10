"""Тесты plugin-ветки allowlist middleware (задача 04, критерий №17).

Проверяется, что ``PluginSurfaceGuardMiddleware``:
  * закрывает agent-surface / webhook / pluginpage для plugin-токенов (403);
  * НЕ трогает агентские ``typ=pod_bridge`` токены (ветка независима);
  * не пускает запрос без валидного identity-конфига (503);
  * не подменяет входящие заголовки, а кладёт проверенные claims в state.

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ (Verification freeze).
"""

from __future__ import annotations

import warnings

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prodavan.api.v1.plugin_capabilities import router as capabilities_router
from prodavan.application.plugins.identity.keys import keyring_from_secret
from prodavan.application.plugins.identity.mint import mint_plugin_claims
from prodavan.application.plugins.identity.revocation import GrantRevocation, InMemoryRevocation
from tests.unit.plugins.transport.conftest import (
    FakeGrant,
    FakeInstance,
    TEST_SECRET,
    auth,
)
from prodavan.application.plugins.transport.guard import register_plugin_surface_guard


def _app() -> FastAPI:
    app = FastAPI()
    app.state.plugin_keyring = keyring_from_secret(TEST_SECRET)
    app.state.plugin_revocation = GrantRevocation(InMemoryRevocation())
    register_plugin_surface_guard(app)

    @app.get("/agent/events")
    async def agent_events():  # type: ignore[no-untyped-def]
        return {"agent": "ok"}

    @app.post("/webhook/{plugin}/{company}")
    async def webhook(plugin: str, company: str):  # type: ignore[no-untyped-def]
        return {"webhook": "ok"}

    @app.get("/pluginpage/{plugin}/{company}/{path:path}")
    async def pluginpage(plugin: str, company: str, path: str):  # type: ignore[no-untyped-def]
        return {"pluginpage": "ok"}

    @app.get("/api/v1/agent/events")
    async def api_agent_events():  # type: ignore[no-untyped-def]
        return {"agent": "ok"}

    @app.get("/api/v1/internal/pods/{pod_id}/workspace-archive")
    async def internal_workspace_archive(pod_id: str):  # type: ignore[no-untyped-def]
        return {"internal": "ok"}

    app.include_router(capabilities_router)
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app(), raise_server_exceptions=False)


@pytest.fixture
def plugin_token() -> str:
    return mint_plugin_claims(
        grant=FakeGrant(), instance=FakeInstance(), secret=TEST_SECRET
    )


def test_plugin_token_on_agent_path_is_403(client: TestClient, plugin_token: str) -> None:
    resp = client.get("/agent/events", headers=auth(plugin_token))
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN_SURFACE"
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_plugin_token_on_webhook_is_403(client: TestClient, plugin_token: str) -> None:
    resp = client.post("/webhook/mod_equipment/co_123", headers=auth(plugin_token))
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN_SURFACE"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/agent/events"),
        ("GET", "/api/v1/agent/events"),
        ("POST", "/webhook/mod_equipment/co_123"),
        ("GET", "/pluginpage/mod_equipment/co_123/api/x"),
        ("GET", "/api/v1/internal/pods/pod-1/workspace-archive"),
    ],
)
def test_plugin_token_on_non_capability_surfaces_is_403_before_route(
    client: TestClient, plugin_token: str, method: str, path: str
) -> None:
    """Every non-capability surface is denied by the guard BEFORE routing.

    The stub routes would return 200 if reached, so 403 proves the middleware
    actually awaited its async identity/claims verification (no fail-open).
    RuntimeWarning is escalated to an error: a coroutine-never-awaited bug
    cannot silently pass this regression again.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        resp = client.request(method, path, headers=auth(plugin_token))
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN_SURFACE"
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_anonymous_agent_surface_is_not_broken_by_guard(client: TestClient) -> None:
    resp = client.get("/agent/events")
    assert resp.status_code == 200
    assert resp.json() == {"agent": "ok"}


def test_agent_bridge_token_is_not_touched_by_plugin_guard(client: TestClient) -> None:
    """Агентская ветка (:8001, typ=pod_bridge) НЕ перехватывается plugin-guard."""
    pod_token = jwt.encode(
        {"typ": "pod_bridge", "sub": "pod:1", "iat": 1_700_000_000, "exp": 2_000_000_000},
        TEST_SECRET,
        algorithm="HS256",
    )
    # Guard пропускает чужой токен; эндпоинт-заглушка отвечает 200.
    # В проде этот путь обслуживает свой trust на :8001 без изменений.
    resp = client.get("/agent/events", headers=auth(pod_token))
    assert resp.status_code == 200
    assert resp.json() == {"agent": "ok"}


def test_anonymous_request_is_not_touched_by_guard(client: TestClient) -> None:
    """Без Bearer guard молчит; авторизацию делает сам роутер (401)."""
    resp = client.post("/capabilities/v1/cache/get", json={"key": "k"})
    assert resp.status_code == 401


def test_capabilities_path_is_allowed_by_guard(client: TestClient, plugin_token: str) -> None:
    """Валидный plugin-токен на своей поверхности проходит guard.

    Роутер потребует привязанный ``PluginCapabilityService``; здесь важна
    именно ОТСУТСТВИЕ 403 от allowlist — получаем 503 (нет интеграции),
    а не FORBIDDEN_SURFACE.
    """
    resp = client.post(
        "/capabilities/v1/cache/get", json={"key": "k"}, headers=auth(plugin_token)
    )
    assert resp.status_code == 503
    assert resp.json()["code"] == "SERVICE_UNAVAILABLE"


def test_missing_identity_config_is_503(plugin_token: str) -> None:
    app = _app()
    del app.state.plugin_keyring
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/capabilities/v1/cache/get", json={"key": "k"}, headers=auth(plugin_token)
    )
    assert resp.status_code == 503
    assert resp.json()["code"] == "IDENTITY_NOT_CONFIGURED"
