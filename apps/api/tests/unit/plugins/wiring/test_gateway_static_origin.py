"""Regression: install path НЕ проксирует plugin static на plugin backend.

Дефект: ``_gateway_runtime`` молча подставлял ``static_http = runtime.backend_http``,
когда override ``gateway_static_http`` не задан. Это нарушает отдельный
static-origin (§5.2/§13.3): статика уходила на plugin backend, хотя он не
always-running и получает capability-токен/cookie-контур, которого у статики
быть не должно.

Контракт после фикса (одинаков для install и platform path):
* без override — fail-closed ``STATIC_ORIGIN_NOT_CONFIGURED`` (503) при запросе
  статики, backend НЕ получает запрос;
* явный override ``gateway_static_http`` используется как есть;
* platform path (``build_plugin_platform``) поведение НЕ меняется.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from prodavan.application.plugins.artifacts.registry import InMemoryRegistryStore
from prodavan.application.plugins.gateway.errors import GatewayError, GatewayErrorCode
from prodavan.application.plugins.gateway.ports import (
    BackendResponse,
    GatewayClaims,
    GatewayGrant,
)
from prodavan.application.plugins.wiring.install import _gateway_runtime
from prodavan.application.plugins.wiring.platform import build_plugin_platform

from .conftest import (
    FakeCosign,
    FakeGrantStore,
    FakeSettings,
    fake_capability_overrides,
    required_platform_port_overrides,
)

ARTIFACT_DIGEST = "sha256:" + "a" * 64
SHELL_ORIGIN = "https://agentscale.example.test"


# --------------------------------------------------------------------------- fakes


class RecordingHttpPort:
    """BackendHttpPort-double: фиксирует каждый send, отвечает 200."""

    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def send(self, request: Any) -> BackendResponse:
        self.requests.append(request)
        return BackendResponse(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"<html>plugin</html>",
        )


class FakeSessions:
    async def resolve(self, *_args: Any, **_kwargs: Any) -> Any:
        return None

    async def employee_in_company(self, *_args: Any, **_kwargs: Any) -> bool:
        return False


class FakeGrants:
    async def active_grant(self, *_args: Any, **_kwargs: Any) -> Any:
        return None

    async def public_pages(self, *_args: Any, **_kwargs: Any) -> frozenset[str]:
        return frozenset()


class FakeSecrets:
    async def webhook_secret(self, *_args: Any, **_kwargs: Any) -> Any:
        return None


class FakeRateLimit:
    async def allow(self, *_args: Any, **_kwargs: Any) -> bool:
        return True

    async def claim_delivery_lease(self, *_args: Any, **_kwargs: Any) -> Any:
        return None

    async def commit_delivery(self, *_args: Any, **_kwargs: Any) -> bool:
        return True

    async def release_delivery(self, *_args: Any, **_kwargs: Any) -> bool:
        return True


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def record(self, event: Any) -> None:
        self.events.append(event)


class FakeActivation:
    async def wake(self, *_args: Any, **_kwargs: Any) -> Any:
        return None

    async def wait_ready(self, *_args: Any, **_kwargs: Any) -> bool:
        return True


# --------------------------------------------------------------------------- helpers


def _build_install_runtime(
    *, static_override: RecordingHttpPort | None = None
) -> tuple[Any, RecordingHttpPort, RecordingHttpPort | None, list[str]]:
    """Собрать ``PluginGatewayRuntime`` через install path ``_gateway_runtime``.

    Все обязательные порты передаются явными test doubles (БЕЗ SQL/Redis/k8s);
    ``runtime.backend_http`` — отдельный recording fake, чтобы доказать, что
    статика туда НЕ уходит.
    """

    backend = RecordingHttpPort()
    runtime = SimpleNamespace(backend_http=backend)
    repos = SimpleNamespace(audit=FakeAudit(), instance_store=None)
    config = SimpleNamespace(shell_origin=SHELL_ORIGIN)
    overrides: dict[str, Any] = {
        "gateway_sessions": FakeSessions(),
        "gateway_grants": FakeGrants(),
        "gateway_secrets": FakeSecrets(),
        "gateway_rate_limit_port": FakeRateLimit(),
        "gateway_audit_port": FakeAudit(),
        "gateway_activation_port": FakeActivation(),
    }
    if static_override is not None:
        overrides["gateway_static_http"] = static_override
    unwired: list[str] = []
    gateway = _gateway_runtime(runtime, repos, object(), config, overrides, unwired)
    return gateway, backend, static_override, unwired


def _static_call_kwargs() -> dict[str, Any]:
    claims = GatewayClaims(
        plugin_id="mod_equipment",
        company_id="co-123",
        subject_id="employee:emp-1",
        plugin_instance_id="inst-alpha-1",
        artifact_digest=ARTIFACT_DIGEST,
        scopes=frozenset({"pluginpage:static"}),
    )
    grant = GatewayGrant(
        grant_id="grant-1",
        plugin_instance_id="inst-alpha-1",
        active=True,
        capability_token="capability-token",
        artifact_digest=ARTIFACT_DIGEST,
        published_artifact_digest=ARTIFACT_DIGEST,
    )
    return {
        "path": "/assets/app.js",
        "method": "GET",
        "headers": {},
        "claims": claims,
        "grant": grant,
        "is_api": False,
    }


def _platform_overrides(
    grant_store: FakeGrantStore, cosign: FakeCosign, admin_ports: Any
) -> dict[str, Any]:
    return {
        "grant_store": grant_store,
        "cosign": cosign,
        "admin_ports": admin_ports,
        "artifact_registry_store": InMemoryRegistryStore(),
        **required_platform_port_overrides(),
        **fake_capability_overrides(),
    }


# --------------------------------------------------------------------------- install path


def test_install_without_static_override_never_uses_plugin_backend() -> None:
    """Без override static_http НЕ равен runtime.backend_http (дефект устранён)."""

    gateway, backend, _static, unwired = _build_install_runtime()

    # Все обязательные порты привязаны; статика — опциональный fail-closed порт.
    assert unwired == []
    assert gateway.proxy.static_http is not backend
    assert gateway.proxy.static_http is not gateway.proxy.backend_http


@pytest.mark.asyncio
async def test_install_without_static_override_is_fail_closed_not_proxied() -> None:
    """Запрос статики → STATIC_ORIGIN_NOT_CONFIGURED; backend не вызывается."""

    gateway, backend, _static, _unwired = _build_install_runtime()

    with pytest.raises(GatewayError) as exc:
        await gateway.proxy.forward_static(**_static_call_kwargs())

    assert exc.value.gateway_code is GatewayErrorCode.STATIC_ORIGIN_NOT_CONFIGURED
    assert backend.requests == []


@pytest.mark.asyncio
async def test_install_static_override_is_used_for_static_requests() -> None:
    """Явный override gateway_static_http принимает статику, backend — нет."""

    static = RecordingHttpPort()
    gateway, backend, _static, unwired = _build_install_runtime(static_override=static)

    assert unwired == []
    assert gateway.proxy.static_http is static
    response = await gateway.proxy.forward_static(**_static_call_kwargs())

    assert response.status_code == 200
    assert len(static.requests) == 1
    assert static.requests[0].path == "/assets/app.js"
    assert backend.requests == []


# --------------------------------------------------------------------------- platform path (unchanged)


def test_platform_without_static_override_keeps_none_static_http(
    monkeypatch: pytest.MonkeyPatch,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: Any,
) -> None:
    """Platform path не изменился: GatewayProxy(static_http=None) — fail-closed."""

    monkeypatch.delenv("PLUGIN_CLAIMS_PREVIOUS_SECRETS", raising=False)
    platform = build_plugin_platform(
        FakeSettings(), **_platform_overrides(grant_store, cosign, admin_ports)
    )

    assert platform is not None
    proxy = platform.gateway[1]
    assert proxy.static_http is None


@pytest.mark.asyncio
async def test_platform_without_static_override_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: Any,
) -> None:
    """Platform path: STATIC_ORIGIN_NOT_CONFIGURED при запросе, как и раньше."""

    monkeypatch.delenv("PLUGIN_CLAIMS_PREVIOUS_SECRETS", raising=False)
    platform = build_plugin_platform(
        FakeSettings(), **_platform_overrides(grant_store, cosign, admin_ports)
    )

    proxy = platform.gateway[1]
    with pytest.raises(GatewayError) as exc:
        await proxy.forward_static(**_static_call_kwargs())
    assert exc.value.gateway_code is GatewayErrorCode.STATIC_ORIGIN_NOT_CONFIGURED


def test_platform_static_override_is_used(
    monkeypatch: pytest.MonkeyPatch,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: Any,
) -> None:
    """Platform path: явный gateway_static_http по-прежнему пробрасывается."""

    monkeypatch.delenv("PLUGIN_CLAIMS_PREVIOUS_SECRETS", raising=False)
    static = RecordingHttpPort()
    overrides = _platform_overrides(grant_store, cosign, admin_ports)
    overrides["gateway_static_http"] = static
    platform = build_plugin_platform(FakeSettings(), **overrides)

    assert platform.gateway[1].static_http is static
