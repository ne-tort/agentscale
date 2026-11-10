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

from prodavan.application.plugins.activation.state_store import InMemoryStateStore
from prodavan.application.plugins.events.contracts import DeliveryClaim, IdempotencyKey
from prodavan.application.plugins.admin.contracts import (
    ArtifactPublicationStatus,
    CapabilitySet,
    SecretPreDeliveryRequest,
)
from prodavan.application.plugins.admin.ports import PluginAdminPorts
from prodavan.application.plugins.capabilities.adapters.secrets import SecretsDeliveryResponse
from prodavan.application.plugins.capabilities.audit import InMemoryAudit
from prodavan.application.plugins.capabilities.contracts import CapabilityName
from prodavan.application.plugins.capabilities.quotas import InMemoryQuota, QuotaUsage
from prodavan.application.plugins.artifacts.verification import VerificationResult
from prodavan.application.plugins.wiring.config import PluginPlatformConfig
from tests.unit.plugins.admin.fakes import Harness

TEST_SECRET = "wiring-test-active-secret-0f31d7"
TEST_OLD_SECRET_1 = "wiring-test-rotated-secret-1"
TEST_OLD_SECRET_2 = "wiring-test-rotated-secret-2"
TEST_STATIC_ORIGIN = "https://plugins.example.test"
TEST_SHELL_ORIGIN = "https://agentscale.example.test"
TEST_DIGEST = "sha256:" + ("ab12cd34" * 8)
TEST_COMPANY_ID = "co_123"
TEST_PLUGIN_ID = "mod_equipment"
TEST_MANIFEST_VERSION = "1.2.0"
TEST_CAPABILITIES = CapabilitySet(capabilities=("cache", "objects"))


# --------------------------------------------------------------------------- settings


@dataclass
class FakeSettings:
    """Structural-замена ``Settings`` со ВСЕМИ PLUGIN_* полями (A3)."""

    plugin_platform_enabled: bool = True
    plugin_claims_secret: str = TEST_SECRET
    plugin_event_signing_secret_ref: str = "vault://unit/plugin-event-signing"
    plugin_claims_ttl_seconds: int = 300
    plugin_claims_issuer: str = "agentscale-plugins"
    plugin_claims_audience: str = "capability-gateway"
    plugin_static_origin: str = TEST_STATIC_ORIGIN
    plugin_shell_origin: str = TEST_SHELL_ORIGIN
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
    plugin_minio_endpoint: str = ""
    plugin_minio_public_endpoint: str = ""
    plugin_minio_admin_access_key_ref: str = ""
    plugin_minio_admin_secret_key_ref: str = ""
    plugin_minio_presign_access_key_ref: str = ""
    plugin_minio_presign_secret_key_ref: str = ""
    plugin_minio_sts_enabled: bool = False
    plugin_minio_sts_endpoint: str = ""
    plugin_minio_sts_session_duration_seconds: int = 900
    plugin_minio_sts_role_arn: str = (
        "arn:aws:iam::000000000000:role/plugin-object-access"
    )
    plugin_minio_checksum_max_bytes: int = 64 * 1024 * 1024
    plugin_minio_region: str = "us-east-1"


class FakeCapabilityAdapter:
    def quota_usage(self, request: object) -> QuotaUsage:
        return QuotaUsage(ops=1, rows=0, bytes=0, keys=1, object_bytes=0, egress_bytes=0)

    def __getattr__(self, name: str) -> Any:
        async def operation(request: object) -> Any:
            return object()
        return operation


def fake_capability_overrides() -> dict[str, object]:
    return {
        f"adapter_{name.value}": FakeCapabilityAdapter()
        for name in CapabilityName
    }


class FakeRawDlq:
    async def publish(self, record: Any) -> bool:
        return True


class FakeDeliveryClaimStore:
    """Explicit test/dev claim override; production composition uses Redis."""

    def __init__(self) -> None:
        self.active: dict[IdempotencyKey, DeliveryClaim] = {}
        self.completed: set[IdempotencyKey] = set()
        self.generation = 0

    async def acquire(self, key: IdempotencyKey, *, lease_seconds: float) -> DeliveryClaim | None:
        if key in self.active and key not in self.completed:
            return None
        self.generation += 1
        claim = DeliveryClaim(
            key=key,
            owner_token=f"wiring-owner-{self.generation}",
            generation=self.generation,
            lease_seconds=lease_seconds,
            completed=key in self.completed,
        )
        self.active[key] = claim
        return claim

    async def renew(self, claim: DeliveryClaim) -> bool:
        return self.active.get(claim.key) == claim

    async def complete(self, claim: DeliveryClaim) -> bool:
        if self.active.get(claim.key) != claim:
            return False
        self.completed.add(claim.key)
        return True

    async def release(self, claim: DeliveryClaim) -> bool:
        if self.active.get(claim.key) != claim:
            return False
        self.active.pop(claim.key, None)
        return True


def required_platform_port_overrides() -> dict[str, object]:
    """Явные test/dev doubles ОБЯЗАТЕЛЬНЫХ durable-портов ``build_plugin_platform``.

    Production in-memory defaults (``InMemoryAudit``/``InMemoryQuota``/
    ``InMemoryStateStore``) удалены: audit/quota/activation_state_store
    передаются явно. Здесь InMemory* — именно осознанные constructor overrides
    для unit-тестов, а не молчаливый default; каждый вызов создаёт СВЕЖИЕ
    объекты, чтобы тест мог проверить identity (без скрытых дублей).
    """
    return {
        "audit": InMemoryAudit(),
        "quota": InMemoryQuota(),
        "activation_state_store": InMemoryStateStore(),
        "event_delivery_claims": FakeDeliveryClaimStore(),
        "event_raw_dlq": FakeRawDlq(),
    }


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

    async def get_grant(self, grant_id: str) -> FakeGrantRow | None:
        self.get_calls.append(grant_id)
        if self.fail_get:
            raise RuntimeError("grant DB is down")
        return self._rows.get(grant_id)

    async def deactivate(self, grant_id: str) -> bool:
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


class FakeSecretDeliveryControl:
    """Deterministic control-plane lifecycle для манифестов без secret declarations."""

    def __init__(self) -> None:
        self.deliver_requests: list[SecretPreDeliveryRequest] = []
        self.revoke_requests: list[SecretPreDeliveryRequest] = []

    async def pre_deliver(
        self, request: SecretPreDeliveryRequest
    ) -> tuple[SecretsDeliveryResponse, ...]:
        self.deliver_requests.append(request)
        return ()

    async def pre_revoke(
        self, request: SecretPreDeliveryRequest
    ) -> tuple[SecretsDeliveryResponse, ...]:
        self.revoke_requests.append(request)
        return ()


@pytest.fixture
def admin_harness() -> Harness:
    """In-memory ports + PUBLISHED artifact + exact unconsumed approval."""
    harness = Harness()
    harness.artifact_registry.seed(
        plugin_id=TEST_PLUGIN_ID,
        manifest_version=TEST_MANIFEST_VERSION,
        artifact_digest=TEST_DIGEST,
        status=ArtifactPublicationStatus.PUBLISHED,
    )
    harness.publication_approvals.seed(
        company_id=TEST_COMPANY_ID,
        plugin_id=TEST_PLUGIN_ID,
        manifest_version=TEST_MANIFEST_VERSION,
        artifact_digest=TEST_DIGEST,
        capabilities=TEST_CAPABILITIES,
        consumed_at=None,
    )
    return harness


@pytest.fixture
def admin_ports(admin_harness: Harness) -> PluginAdminPorts:
    """Полные typed ports с webhook store и обоими secret lifecycle hooks."""
    return PluginAdminPorts(
        tenant_db=admin_harness.tenant_db,
        object_store=admin_harness.object_store,
        artifact_registry=admin_harness.artifact_registry,
        instance_store=admin_harness.instance_store,
        grant_store=admin_harness.grant_store,
        revocation=admin_harness.revocation,
        mint=admin_harness.mint,
        activation=admin_harness.activation,
        routes=admin_harness.routes,
        audit=admin_harness.audit,
        platform_admin=admin_harness.platform_admin,
        publication_approvals=admin_harness.publication_approvals,
        webhook_secrets=admin_harness.webhook_secrets,
        secret_delivery=FakeSecretDeliveryControl(),
        installation_scopes=admin_harness.installation_scopes,
        update_lock=admin_harness.update_lock,
    )


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
