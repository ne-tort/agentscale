"""Composition tests for the real tenant admin adapter; no network IO."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from prodavan.application.plugins.tenantdb.errors import ADMIN_UNAVAILABLE, TenantDbError
from prodavan.application.plugins.wiring.install import (
    TENANT_DB_ADMIN_LIFECYCLE_STATE_KEY,
    TENANT_DB_ADMIN_STATE_KEY,
    _Composer,
    _TenantDbAdminLifecycle,
    _register_tenant_db_admin_lifecycle,
    _tenant_db_admin,
    _tenant_db_secrets,
    _zone_services,
)
from prodavan.config.settings import Settings
from prodavan.infrastructure.plugins.tenantdb import AsyncpgAdminConnection
from tests.unit.plugins.tenantdb.conftest import FakeAdminConnection, FakeSecretStore
from tests.unit.plugins.wiring.conftest import FakeCosign, FakeSettings

ADMIN_DSN = "postgresql://admin:wiring-secret@tenant-db.internal:5432/postgres"


def test_typed_settings_hide_admin_url_and_reject_tls_disable() -> None:
    settings = Settings(
        _env_file=None,
        plugin_platform_enabled=False,
        plugin_tenant_db_admin_url=ADMIN_DSN,
        plugin_tenant_db_admin_pool_min_size=1,
        plugin_tenant_db_admin_pool_max_size=3,
    )
    assert "plugin_tenant_db_admin_url" not in settings.model_dump()
    assert ADMIN_DSN not in repr(settings)
    assert "wiring-secret" not in repr(settings)
    assert settings.plugin_tenant_db_admin_tls_mode == "verify-full"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, plugin_platform_enabled=False, plugin_tenant_db_admin_tls_mode="disable")


def test_tenant_admin_uppercase_env_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLUGIN_TENANT_DB_ADMIN_URL", ADMIN_DSN)
    monkeypatch.setenv("PLUGIN_TENANT_DB_ADMIN_TLS_MODE", "require")
    monkeypatch.setenv("PLUGIN_TENANT_DB_ADMIN_CONNECT_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("PLUGIN_TENANT_DB_ADMIN_COMMAND_TIMEOUT_SECONDS", "11")
    monkeypatch.setenv("PLUGIN_TENANT_DB_ADMIN_POOL_MIN_SIZE", "2")
    monkeypatch.setenv("PLUGIN_TENANT_DB_ADMIN_POOL_MAX_SIZE", "5")
    settings = Settings(_env_file=None, plugin_platform_enabled=False)
    assert settings.plugin_tenant_db_admin_url == ADMIN_DSN
    assert settings.plugin_tenant_db_admin_tls_mode == "require"
    assert settings.plugin_tenant_db_admin_connect_timeout_seconds == 7.0
    assert settings.plugin_tenant_db_admin_command_timeout_seconds == 11.0
    assert settings.plugin_tenant_db_admin_pool_min_size == 2
    assert settings.plugin_tenant_db_admin_pool_max_size == 5


def test_full_config_builds_real_adapter_without_dsn_logs(caplog: pytest.LogCaptureFixture) -> None:
    admin = _tenant_db_admin(FakeSettings(plugin_tenant_db_admin_url=ADMIN_DSN))
    assert isinstance(admin, AsyncpgAdminConnection)
    assert "wiring-secret" not in caplog.text
    assert ADMIN_DSN not in caplog.text


def test_missing_or_bad_tls_config_is_safe_unwired(caplog: pytest.LogCaptureFixture) -> None:
    assert _tenant_db_admin(FakeSettings(plugin_tenant_db_admin_url="")) is None
    bad_tls = FakeSettings(
        plugin_tenant_db_admin_url=ADMIN_DSN,
    )
    # Structural fakes can inject deployment-specific TLS fields just like Settings.
    bad_tls.plugin_tenant_db_admin_tls_ca_file = "missing-ca.pem"
    assert _tenant_db_admin(bad_tls) is None
    assert "wiring-secret" not in caplog.text
    assert "tenant-db.internal" not in caplog.text
    assert "missing-ca.pem" not in caplog.text


def test_secret_wiring_failure_does_not_log_dsn(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_store() -> Any:
        raise RuntimeError(f"backend rejected {ADMIN_DSN}")

    monkeypatch.setattr("prodavan.infrastructure.secrets.store.get_secret_store", fail_store)
    assert _tenant_db_secrets() is None
    assert ADMIN_DSN not in caplog.text
    assert "wiring-secret" not in caplog.text


def _services(
    *,
    monkeypatch: pytest.MonkeyPatch,
    config: Any,
    admin: Any,
) -> tuple[dict[str, Any], list[str]]:
    monkeypatch.setattr(
        "prodavan.application.plugins.wiring.install._activation_mint",
        lambda _repos, _config: object(),
    )
    scale = object()
    runtime = SimpleNamespace(
        cosign=FakeCosign(),
        scale=scale,
        readiness=scale,
        state_store=object(),
        proxy=object(),
    )
    repos = SimpleNamespace(artifact_store=object(), audit=object())
    overrides = {
        "tenant_db_secrets": FakeSecretStore(),
        "object_buckets": object(),
        "activation_mint": object(),
    }
    if admin is not None:
        overrides["tenant_db_admin"] = admin
    unwired: list[str] = []
    services = _zone_services(runtime, repos, config, overrides, unwired)
    return services, unwired


def test_zone_service_uses_real_admin_not_unwired(
    monkeypatch: pytest.MonkeyPatch,
    enabled_config: Any,
) -> None:
    admin = FakeAdminConnection()
    services, unwired = _services(monkeypatch=monkeypatch, config=enabled_config, admin=admin)
    assert services["tenant_db"]._admin is admin
    assert "tenant_db_admin" not in unwired


async def test_missing_admin_is_typed_failure_and_not_ready(
    monkeypatch: pytest.MonkeyPatch,
    enabled_config: Any,
) -> None:
    services, unwired = _services(monkeypatch=monkeypatch, config=enabled_config, admin=None)
    assert services["tenant_db"]._admin is None
    assert "tenant_db_admin" in unwired
    with pytest.raises(TenantDbError) as caught:
        await services["tenant_db"].ensure_company_database("co_123")
    assert caught.value.code == ADMIN_UNAVAILABLE
    assert await _TenantDbAdminLifecycle(None).health() is False


class _LifecycleAdmin:
    def __init__(self) -> None:
        self.open_calls = 0
        self.close_calls = 0
        self.healthy = False

    async def open(self) -> None:
        self.open_calls += 1
        self.healthy = True

    async def close(self) -> None:
        self.close_calls += 1
        self.healthy = False

    async def health(self) -> bool:
        return self.healthy


class _Manager:
    def __init__(self) -> None:
        self.resources: list[Any] = []

    def register(self, resource: Any) -> None:
        self.resources.append(resource)

    def get(self, name: str) -> Any | None:
        return next((item for item in self.resources if item.name == name), None)


async def test_shared_lifecycle_opens_and_closes_one_adapter() -> None:
    admin = _LifecycleAdmin()
    resource = _TenantDbAdminLifecycle(admin)
    manager = _Manager()
    app = SimpleNamespace(state=SimpleNamespace(lifespan_manager=manager))
    composer = _Composer(app)

    _register_tenant_db_admin_lifecycle(app, resource, composer)
    _register_tenant_db_admin_lifecycle(app, resource, composer)
    assert manager.resources == [resource]
    assert getattr(app.state, TENANT_DB_ADMIN_STATE_KEY) is admin
    assert getattr(app.state, TENANT_DB_ADMIN_LIFECYCLE_STATE_KEY) is resource

    await resource.startup()
    assert admin.open_calls == 1
    assert await resource.health() is True
    await resource.shutdown()
    assert admin.close_calls == 1
    assert await resource.health() is False
