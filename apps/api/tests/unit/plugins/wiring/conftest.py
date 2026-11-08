"""Общие фикстуры/фейки для unit-тестов wiring (F11).

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ (verification freeze, план миграции §0.2,
дизайн v4 §9). Ни один фейк не делает IO: БД grant'ов, cosign, адаптеры,
сессии и k8s подменяются объектами в памяти.

Важно: ``FakeGrantStore`` хранит статус ``CapabilityGrantRow.status`` (A10)
и умеет «падать» по флагу — это нужно для проверки fail-closed при
недоступности хранилища.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from prodavan.application.plugins.admin.ports import PluginAdminPorts
from prodavan.application.plugins.artifacts.verification import VerificationResult
from prodavan.application.plugins.wiring.config import PluginPlatformConfig

TEST_SECRET = "wiring-test-active-secret-0f31d7"
TEST_OLD_SECRET_1 = "wiring-test-rotated-secret-1"
TEST_OLD_SECRET_2 = "wiring-test-rotated-secret-2"
TEST_STATIC_ORIGIN = "https://plugins.example.test"
TEST_DIGEST = "sha256:" + ("ab12cd34" * 8)


# --------------------------------------------------------------------------- settings


@dataclass
class FakeSettings:
    """Structural-замена ``Settings`` со ВСЕМИ PLUGIN_* полями (A3)."""

    plugin_platform_enabled: bool = True
    plugin_claims_secret: str = TEST_SECRET
    plugin_claims_ttl_seconds: int = 300
    plugin_claims_issuer: str = "agentscale-plugins"
    plugin_claims_audience: str = "capability-gateway"
    plugin_static_origin: str = TEST_STATIC_ORIGIN
    plugin_tenant_db_admin_url: str = ""
    plugin_tenant_db_host: str = "tenant-postgres"
    plugin_tenant_db_port: int = 5432
    plugin_egress_mode: str = "none"
    plugin_egress_approved: bool = False
    plugin_idle_ttl_seconds: int = 300
    plugin_ready_timeout_seconds: int = 120
    plugin_max_concurrent_wakes: int = 2
    plugin_oci_registry: str = "ghcr.io/ne-tort/plugins"
    plugin_cosign_identity: str = "https://github.com/ne-tort/agentscale/.github/workflows/x@refs/heads/main"
    plugin_cosign_issuer: str = "https://token.actions.githubusercontent.com"
    plugin_min_platform_version: str = "4.0.0"


@pytest.fixture
def fake_settings() -> FakeSettings:
    return FakeSettings()


@pytest.fixture
def enabled_config(monkeypatch: pytest.MonkeyPatch) -> PluginPlatformConfig:
    """Включённый конфиг без ротационных ключей (env мог быть задан снаружи)."""
    monkeypatch.delenv("PLUGIN_CLAIMS_PREVIOUS_SECRETS", raising=False)
    return PluginPlatformConfig.from_settings(FakeSettings())


@pytest.fixture
def disabled_config(monkeypatch: pytest.MonkeyPatch) -> PluginPlatformConfig:
    monkeypatch.delenv("PLUGIN_CLAIMS_PREVIOUS_SECRETS", raising=False)
    return PluginPlatformConfig.from_settings(FakeSettings(plugin_platform_enabled=False))


# --------------------------------------------------------------------------- grant store (A10)


@dataclass
class FakeGrantRow:
    """Имитация ``CapabilityGrantRow.status`` из задачи A10."""

    status: str = "active"
    grant_id: str = "grant_01"
    revoked_at: datetime | None = None
    issued_at: datetime | None = None
    expires_at: datetime | None = None


class FakeGrantStore:
    """Порт хранилища grant'ов: in-memory статусы + управляемый сбой."""

    def __init__(self, *rows: FakeGrantRow) -> None:
        self._rows: dict[str, FakeGrantRow] = {row.grant_id: row for row in rows} or {
            "grant_01": FakeGrantRow()
        }
        self.fail_get = False
        self.fail_deactivate = False
        self.deactivate_result = True
        self.get_calls: list[str] = []
        self.deactivate_calls: list[str] = []

    def get_grant(self, grant_id: str) -> FakeGrantRow | None:
        self.get_calls.append(grant_id)
        if self.fail_get:
            raise RuntimeError("grant DB is down")
        return self._rows.get(grant_id)

    def deactivate(self, grant_id: str) -> bool:
        self.deactivate_calls.append(grant_id)
        if self.fail_deactivate:
            raise RuntimeError("grant DB is down")
        if not self.deactivate_result:
            return False
        row = self._rows.get(grant_id)
        if row is None:
            return False
        row.status = "revoked"
        row.revoked_at = datetime.now(UTC)
        return True


@pytest.fixture
def grant_store() -> FakeGrantStore:
    return FakeGrantStore()


# --------------------------------------------------------------------------- cosign / manifest


class FakeCosign:
    """``CosignPort``, который «проверяет» подпись без CLI."""

    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[tuple[str, str, str]] = []

    def verify(self, image_ref: str, identity: str, issuer: str) -> VerificationResult:
        self.calls.append((image_ref, identity, issuer))
        if not self.ok:
            return VerificationResult(verified=False, detail="signature mismatch")
        return VerificationResult(
            verified=True,
            signed_digest=TEST_DIGEST,
            identity=identity,
            issuer=issuer,
            sbom_verified=True,
            provenance_verified=True,
            detail="ok",
        )


@pytest.fixture
def cosign() -> FakeCosign:
    return FakeCosign()


# --------------------------------------------------------------------------- admin ports (задача 12)


class _AnyPort:
    """Универсальный fake порта: любой метод = записать вызов, вернуть None."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, method: str) -> Any:
        def record(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((method, args, kwargs))
            return None

        record.__name__ = method
        return record


@pytest.fixture
def admin_ports() -> PluginAdminPorts:
    """Полная связка портов (все 10 полей обязательны, fail-closed)."""
    # dataclass frozen/slots: поля positional или keyword — используем keyword.
    names = (
        "tenant_db",
        "object_store",
        "artifact_registry",
        "grant_store",
        "revocation",
        "mint",
        "activation",
        "routes",
        "audit",
        "platform_admin",
    )
    return PluginAdminPorts(**{name: _AnyPort(name) for name in names})


# --------------------------------------------------------------------------- app/request


class FakeState:
    """Простой ``app.state`` (Starlette State ведёт себя так же)."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_data":
            object.__setattr__(self, name, value)
        else:
            self._data[name] = value

    def __getattr__(self, name: str) -> Any:
        try:
            return self._data[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __delattr__(self, name: str) -> None:
        self._data.pop(name, None)

    def __contains__(self, name: str) -> bool:
        return name in self._data


class FakeApp:
    def __init__(self) -> None:
        self.state = FakeState()


class FakeRequest:
    """Минимальный ``Request`` для ``get_plugin_platform`` (нужен только .app)."""

    def __init__(self, app: FakeApp) -> None:
        self.app = app


@pytest.fixture
def fake_app() -> FakeApp:
    return FakeApp()


@pytest.fixture
def fake_request(fake_app: FakeApp) -> FakeRequest:
    return FakeRequest(fake_app)
