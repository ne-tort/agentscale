"""Фикстуры HTTP-тестов ``/capabilities/v1/*`` (задача 04).

Тесты используют ``fastapi.testclient.TestClient`` и РЕАЛЬНЫЙ trust-контур
задачи 02 (mint/verify/allowlist/revocation), чтобы проверить именно стык:
Bearer -> PluginClaims -> surface -> typed body -> PluginCapabilityService.

``PluginCapabilityService`` задачи 03 подменяется ``FakeCapabilityService``
через ``app.state.plugin_capability_service``: так тесты не зависят от чужих
адаптеров/аудита, но проверяют ВЕСЬ HTTP-контракт (статусы, ``code``,
``{decision, data}``, игнорирование ``X-Company-ID``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from prodavan.api.v1.plugin_capabilities import router as plugin_capabilities_router
from prodavan.application.plugins.identity.allowlist import is_plugin_capability_token
from prodavan.application.plugins.identity.keys import keyring_from_secret
from prodavan.application.plugins.identity.mint import mint_plugin_claims, peek_plugin_token
from prodavan.application.plugins.identity.revocation import GrantRevocation, InMemoryRevocation
from prodavan.application.plugins.transport.deps import (
    BEARER_PREFIX,
    get_plugin_claims,
    resolve_identity_config,
)
from prodavan.application.plugins.transport.errors import (
    PluginTransportError,
    plugin_problem_response,
)

TEST_SECRET = "plugin-transport-test-secret-explicit-7b21"
TEST_DIGEST = "sha256:" + ("c0ffee12" * 8)
TEST_COMPANY = "co_123"
TEST_INSTANCE = "inst_abc"

# Полный набор scope: все эндпоинты роутера должны быть достижимы с валидным
# токеном. В реальном токене scopes приходят из CapabilityGrant (§4.2).
ALL_SCOPES = (
    "cache",
    "objects",
    "docs",
    "events",
    "jobs",
    "pods",
    "mcp",
    "secrets",
    "network",
)


@dataclass(frozen=True)
class FakeGrant:
    grant_id: str = "grant_01"
    plugin_id: str = "mod_equipment"
    company_id: str = TEST_COMPANY
    scopes: tuple[str, ...] = ALL_SCOPES
    manifest_version: str = "1.2.0"
    artifact_digest: str = TEST_DIGEST
    quota_ref: str = "co_123/plan_pro"


@dataclass(frozen=True)
class FakeInstance:
    plugin_instance_id: str = TEST_INSTANCE
    cabinet_id: str | None = "cab_456"
    project_id: str | None = None


@dataclass(frozen=True)
class FakeDecision:
    """Имитация ``PluginGatewayDecision`` задачи 03."""

    decision: str
    code: str
    data: Any = None
    reason: str | None = None
    grant_id: str | None = None


@dataclass
class FakeCapabilityService:
    """Фиктивный сервис: фиксирует вызовы и возвращает настраиваемое решение.

    Позволяет тестировать маппинг ``code`` -> HTTP (429/403/503/422/401) без
    реального ядра gateway. Решения штатного deny НЕ исключения (как у задачи 03).
    """

    result: Any = field(default_factory=lambda: FakeDecision("allow", "allowed", {"ok": True}))
    calls: list[dict[str, Any]] = field(default_factory=list)
    raise_exc: BaseException | None = None

    def invoke(self, *, claims: Any, capability: str, operation: str, request: Any) -> Any:
        self.calls.append(
            {
                "claims": claims,
                "capability": capability,
                "operation": operation,
                "request": request,
            }
        )
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.result


@pytest.fixture
def secret() -> str:
    return TEST_SECRET


@pytest.fixture
def keyring(secret: str):
    return keyring_from_secret(secret)


@pytest.fixture
def revocation():
    return GrantRevocation(InMemoryRevocation())


@pytest.fixture
def token(secret: str) -> str:
    return mint_plugin_claims(grant=FakeGrant(), instance=FakeInstance(), secret=secret)


@pytest.fixture
def service() -> FakeCapabilityService:
    return FakeCapabilityService()


class PluginTokenGuardMiddleware(BaseHTTPMiddleware):
    """Тестовая имитация F11-интеграции родителя (ветка Plugin Gateway).

    В проде родитель подключает такую же проверку в middleware: если в запросе
    plugin-токен, он обязан пройти ``get_plugin_claims`` (verify + revocation +
    ``assert_plugin_surface``) ДО роутинга. Это и даёт 403 на ``/agent/events``
    даже когда путь не входит в наш route-group (критерий №17). Тест только
    воспроизводит контракт интеграции, не подменяя продуктовый код.
    """

    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        header = request.headers.get("Authorization") or ""
        if header.startswith(BEARER_PREFIX):
            token = header[len(BEARER_PREFIX) :].strip()
            if token and is_plugin_capability_token(token):
                state = request.app.state
                config = PluginIdentityConfig(
                    keyring=state.plugin_keyring,
                    revocation=state.plugin_revocation,
                )
                try:
                    get_plugin_claims(request, config)
                except PluginTransportError as exc:
                    return plugin_problem_response(exc.problem, request)
        return await call_next(request)


@pytest.fixture
def app(keyring, revocation, service: FakeCapabilityService) -> FastAPI:
    """Отдельное приложение Plugin Gateway (НЕ main.py, НЕ main_pod.py)."""
    application = FastAPI()
    application.state.plugin_keyring = keyring
    application.state.plugin_revocation = revocation
    application.state.plugin_capability_service = service
    application.add_middleware(PluginTokenGuardMiddleware)
    # КРИТИЧНО: без внешнего /api/v1 префикса — allowlist задачи 02 разрешает
    # только /capabilities/v1/* (см. отчёт родителю).
    application.include_router(plugin_capabilities_router)
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def now_epoch() -> int:
    return int(time.time())


def peek(token: str) -> dict[str, Any] | None:
    return peek_plugin_token(token)


__all__ = [
    "ALL_SCOPES",
    "FakeCapabilityService",
    "FakeDecision",
    "FakeGrant",
    "FakeInstance",
    "TEST_COMPANY",
    "TEST_DIGEST",
    "TEST_INSTANCE",
    "TEST_SECRET",
    "auth",
    "peek",
]
