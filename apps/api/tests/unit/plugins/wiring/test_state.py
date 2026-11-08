"""F11: ``attach_plugin_platform`` / ``get_plugin_platform`` (app.state, 503).

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ (verification freeze).
"""

from __future__ import annotations

import pytest
from starlette.exceptions import HTTPException

from prodavan.application.plugins.transport.deps import PluginIdentityConfig
from prodavan.application.plugins.wiring.platform import (
    PluginPlatform,
    build_plugin_platform,
)
from prodavan.application.plugins.wiring.state import (
    PLATFORM_UNAVAILABLE_CODE,
    REQUIRED_STATE_KEYS,
    STATE_KEYS,
    PluginPlatformNotAttached,
    attach_plugin_platform,
    detach_plugin_platform,
    get_capability_service_factory,
    get_plugin_platform,
)
from tests.unit.plugins.wiring.conftest import (
    FakeApp,
    FakeCosign,
    FakeGrantStore,
    FakeRequest,
    FakeSettings,
)


def _platform(
    monkeypatch: pytest.MonkeyPatch,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> PluginPlatform:
    monkeypatch.delenv("PLUGIN_CLAIMS_PREVIOUS_SECRETS", raising=False)
    platform = build_plugin_platform(
        FakeSettings(),
        grant_store=grant_store,
        cosign=cosign,
        admin_ports=admin_ports,
    )
    assert platform is not None
    return platform


def test_attach_puts_every_required_key_in_app_state(
    monkeypatch: pytest.MonkeyPatch,
    fake_app: FakeApp,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    platform = _platform(monkeypatch, grant_store, cosign, admin_ports)
    returned = attach_plugin_platform(fake_app, platform)

    assert returned is platform
    for key in STATE_KEYS:
        assert hasattr(fake_app.state, key), f"app.state.{key} is missing"
    for key in REQUIRED_STATE_KEYS:
        assert getattr(fake_app.state, key) is not None


def test_attach_sets_task04_keys(
    monkeypatch: pytest.MonkeyPatch,
    fake_app: FakeApp,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    platform = _platform(monkeypatch, grant_store, cosign, admin_ports)
    attach_plugin_platform(fake_app, platform)

    assert fake_app.state.plugin_platform is platform
    assert fake_app.state.plugin_capability_service is platform.capability_service
    assert fake_app.state.plugin_keyring is platform.keyring
    assert fake_app.state.plugin_revocation is platform.revocation

    factory = fake_app.state.plugin_capability_service_factory
    assert callable(factory)
    assert factory() is platform.capability_service
    assert factory(FakeRequest(fake_app)) is platform.capability_service

    identity = fake_app.state.plugin_identity
    assert isinstance(identity, PluginIdentityConfig)
    assert identity.keyring is platform.keyring
    assert identity.revocation is platform.revocation


def test_attach_also_exposes_zone_services(
    monkeypatch: pytest.MonkeyPatch,
    fake_app: FakeApp,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    platform = _platform(monkeypatch, grant_store, cosign, admin_ports)
    attach_plugin_platform(fake_app, platform)

    assert fake_app.state.plugin_artifact_verifier is platform.artifact_verifier
    assert fake_app.state.plugin_artifact_registry is platform.artifact_registry
    assert fake_app.state.plugin_adapter_registry is platform.registry
    assert fake_app.state.plugin_tenant_db is platform.tenant_db
    assert fake_app.state.plugin_object_store is platform.object_store
    assert fake_app.state.plugin_egress is platform.egress
    assert fake_app.state.plugin_activator is platform.activator
    assert fake_app.state.plugin_gateway_auth is platform.gateway[0]
    assert fake_app.state.plugin_gateway_proxy is platform.gateway[1]
    assert fake_app.state.plugin_admin_service is platform.admin
    assert fake_app.state.plugin_event_dispatcher is platform.event_dispatcher
    assert fake_app.state.plugin_jwks == platform.jwks


def test_attach_none_is_refused(fake_app: FakeApp) -> None:
    """Выключенную платформу НЕ привязываем «пустышкой» (роутеры -> 503)."""
    with pytest.raises(PluginPlatformNotAttached, match="disabled"):
        attach_plugin_platform(fake_app, None)
    assert not hasattr(fake_app.state, "plugin_platform")


def test_attach_disabled_platform_object_is_refused(fake_app: FakeApp) -> None:
    # ``PluginPlatform`` с enabled=False собрать штатно нельзя, но если кто-то
    # передаст такой объект (например, через dataclasses.replace в тесте) —
    # привязка обязана отказать.
    class Disabled:
        enabled = False

    with pytest.raises(PluginPlatformNotAttached):
        attach_plugin_platform(fake_app, Disabled())  # type: ignore[arg-type]
    assert not hasattr(fake_app.state, "plugin_platform")


def test_attach_wrong_type_is_refused(fake_app: FakeApp) -> None:
    with pytest.raises(PluginPlatformNotAttached):
        attach_plugin_platform(fake_app, object())  # type: ignore[arg-type]


def test_get_plugin_platform_on_unattached_app_returns_503(
    fake_app: FakeApp,
) -> None:
    request = FakeRequest(fake_app)
    with pytest.raises(HTTPException) as ei:
        get_plugin_platform(request)
    assert ei.value.status_code == 503
    assert ei.value.detail["code"] == PLATFORM_UNAVAILABLE_CODE


def test_get_plugin_platform_on_broken_requests_returns_503() -> None:
    with pytest.raises(HTTPException) as ei:
        get_plugin_platform(object())  # type: ignore[arg-type]
    assert ei.value.status_code == 503


def test_get_plugin_platform_after_attach_returns_platform(
    monkeypatch: pytest.MonkeyPatch,
    fake_app: FakeApp,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    platform = _platform(monkeypatch, grant_store, cosign, admin_ports)
    attach_plugin_platform(fake_app, platform)
    assert get_plugin_platform(FakeRequest(fake_app)) is platform


def test_get_plugin_platform_after_detach_returns_503(
    monkeypatch: pytest.MonkeyPatch,
    fake_app: FakeApp,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    platform = _platform(monkeypatch, grant_store, cosign, admin_ports)
    attach_plugin_platform(fake_app, platform)
    detach_plugin_platform(fake_app)
    with pytest.raises(HTTPException) as ei:
        get_plugin_platform(FakeRequest(fake_app))
    assert ei.value.status_code == 503


def test_disabled_platform_in_state_returns_503(fake_app: FakeApp) -> None:
    class DisabledPlatform:
        enabled = False

    fake_app.state.plugin_platform = DisabledPlatform()
    with pytest.raises(HTTPException) as ei:
        get_plugin_platform(FakeRequest(fake_app))
    assert ei.value.status_code == 503


def test_factory_dependency_returns_service(
    monkeypatch: pytest.MonkeyPatch,
    fake_app: FakeApp,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    platform = _platform(monkeypatch, grant_store, cosign, admin_ports)
    attach_plugin_platform(fake_app, platform)
    assert get_capability_service_factory(FakeRequest(fake_app)) is platform.capability_service


def test_factory_dependency_returns_503_when_broken(fake_app: FakeApp) -> None:
    fake_app.state.plugin_capability_service_factory = lambda: None
    with pytest.raises(HTTPException) as ei:
        get_capability_service_factory(FakeRequest(fake_app))
    assert ei.value.status_code == 503

    fake_app.state.plugin_capability_service_factory = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    with pytest.raises(HTTPException) as ei2:
        get_capability_service_factory(FakeRequest(fake_app))
    assert ei2.value.status_code == 503
