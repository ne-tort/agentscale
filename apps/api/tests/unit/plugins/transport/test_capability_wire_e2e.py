"""Сквозной HTTP-путь: реальный ``PluginCapabilityService`` за роутером.

Остальные HTTP-тесты ``/capabilities/v1/*`` подменяют сервис фиктивным, поэтому
реальный authorize/invoke pipeline никогда не встречался с реальным transport.
Здесь проверяется именно стык: Bearer -> PluginClaims -> surface guard ->
typed body -> настоящий ``PluginCapabilityService`` -> адаптер -> решение.

Адаптер и квоты — явные тестовые порты конструктора (не production-дефолты);
транспорт, identity, guard, авторизация и сервис — настоящий продуктовый код.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from prodavan.api.v1.plugin_capabilities import router as plugin_capabilities_router
from prodavan.application.plugins.capabilities.contracts import (
    CacheGetRequest,
    CacheGetResponse,
)
from prodavan.application.plugins.capabilities.audit import InMemoryAudit
from prodavan.application.plugins.capabilities.ports import AdapterRegistry
from prodavan.application.plugins.capabilities.quotas import (
    InMemoryQuota,
    PluginQuota,
    QuotaUsage,
)
from prodavan.application.plugins.capabilities.service import PluginCapabilityService
from prodavan.application.plugins.identity.keys import keyring_from_secret
from prodavan.application.plugins.identity.mint import mint_plugin_claims
from prodavan.application.plugins.identity.revocation import GrantRevocation, InMemoryRevocation
from prodavan.application.plugins.transport.guard import register_plugin_surface_guard

SECRET = "plugin-wire-test-secret-explicit-4f19"
DIGEST = "sha256:" + ("a1b2c3d4" * 8)
COMPANY = "co_wire"
INSTANCE = "inst_wire00000000000000000000000001"
GRANT_ID = "grant_wire01"
PLUGIN_ID = "wire_plugin"
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _Grant:
    grant_id: str = GRANT_ID
    plugin_id: str = PLUGIN_ID
    company_id: str = COMPANY
    scopes: tuple[str, ...] = ("cache",)
    manifest_version: str = "1.2.0"
    artifact_digest: str = DIGEST
    quota_ref: str = "co_wire/plan"


@dataclass(frozen=True, slots=True)
class _Instance:
    plugin_instance_id: str = INSTANCE
    cabinet_id: str | None = None
    project_id: str | None = None


@dataclass(frozen=True, slots=True)
class _GrantRecord:
    grant_id: str = GRANT_ID
    company_id: str = COMPANY
    plugin_instance_id: str = INSTANCE
    plugin_id: str = PLUGIN_ID
    manifest_version: str = "1.2.0"
    artifact_digest: str = DIGEST
    capabilities: tuple[str, ...] = ("cache",)
    status: str = "active"
    created_at: datetime = NOW
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    quotas: object = PluginQuota(ops_per_minute=100)


class _GrantStore:
    """Durable-подобный источник grant'а с тем же контрактом, что у SQL."""

    def __init__(self, record: _GrantRecord | None) -> None:
        self.record = record

    async def get_by_grant_id(self, *, grant_id: str) -> Any:
        return self.record if self.record and self.record.grant_id == grant_id else None


class _ActiveRevocation:
    async def is_active(self, grant_id: str, *, now: datetime) -> bool:
        return True


class _CacheAdapter:
    """Тестовый адаптер: считает реальные вызовы, пришедшие через HTTP."""

    def __init__(self) -> None:
        self.calls = 0

    def quota_usage(self, request: CacheGetRequest) -> QuotaUsage:
        return QuotaUsage(ops=1, rows=0, bytes=0, keys=1, object_bytes=0, egress_bytes=0)

    async def get(self, request: CacheGetRequest) -> CacheGetResponse:
        self.calls += 1
        return CacheGetResponse(
            request_id=request.envelope.request_id, ok=True, digest=DIGEST, found=True,
        )

    async def set(self, request: object) -> CacheGetResponse:
        self.calls += 1
        return CacheGetResponse(request_id="req-wire", ok=True, digest=DIGEST)

    delete = incr = publish = set


def _build(record: _GrantRecord | None = _GrantRecord()) -> tuple[FastAPI, _CacheAdapter]:
    registry = AdapterRegistry()
    cache = _CacheAdapter()
    registry.register("cache", cache)
    service = PluginCapabilityService(
        registry=registry,
        grant_store=_GrantStore(record),  # type: ignore[arg-type]
        quota=InMemoryQuota(now=NOW),
        audit=InMemoryAudit(),  # type: ignore[arg-type]
        revocation=_ActiveRevocation(),  # type: ignore[arg-type]
    )
    application = FastAPI()
    application.state.plugin_keyring = keyring_from_secret(SECRET)
    application.state.plugin_revocation = GrantRevocation(InMemoryRevocation())
    application.state.plugin_capability_service = service
    register_plugin_surface_guard(application)
    application.include_router(plugin_capabilities_router)
    return application, cache


def _token(secret: str = SECRET) -> str:
    return mint_plugin_claims(grant=_Grant(), instance=_Instance(), secret=secret)


def test_granted_token_reaches_real_service_and_adapter() -> None:
    app, cache = _build()
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/capabilities/v1/cache/get",
        json={"key": "wire-key"},
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["decision"] == "allow"
    # Реальный сервис действительно дошёл до адаптера.
    assert cache.calls == 1


def test_missing_durable_grant_is_denied_inside_the_real_service() -> None:
    app, cache = _build(record=None)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/capabilities/v1/cache/get",
        json={"key": "wire-key"},
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert response.status_code in {401, 403, 404}, response.text
    # Отказ приходит по problem-контракту транспорта, а не как DecisionResponse.
    body = response.json()
    assert "decision" not in body or body["decision"] != "allow"
    # Отказ случился ДО адаптера: никаких побочных эффектов.
    assert cache.calls == 0


def test_token_signed_with_another_secret_is_rejected_before_the_service() -> None:
    app, cache = _build()
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/capabilities/v1/cache/get",
        json={"key": "wire-key"},
        headers={"Authorization": f"Bearer {_token('another-secret-value-x1')}"},
    )
    assert response.status_code in {401, 403}, response.text
    assert cache.calls == 0


def test_surface_is_closed_without_a_bearer_token() -> None:
    app, cache = _build()
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/capabilities/v1/cache/get", json={"key": "wire-key"})
    assert response.status_code in {401, 403}, response.text
    assert cache.calls == 0
