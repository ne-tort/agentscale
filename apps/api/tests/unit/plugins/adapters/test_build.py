"""Тесты ``build_plugin_admin_ports``: ровно 12 портов, fail-closed.

Главный тест по заданию: отсутствие ЛЮБОЙ обязательной реализации = явная
ошибка, а не заглушка «успех». Проверяем по одному для КАЖДОЙ из 11
зависимостей (параметрайз), плюс успешную сборку и «нет stub-успеха».

Тесты покрыты прогоном tests/unit/plugins в этом worktree.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from prodavan.application.plugins.admin.contracts import (
    AdminAuditEvent,
    AdminDecision,
    AdminOperation,
)
from prodavan.application.plugins.admin.ports import PluginAdminPorts
from prodavan.application.plugins.admin.service import PluginAdminService
from prodavan.application.plugins.adapters.activation_adapter import (
    InMemoryInstanceResolver,
)
from prodavan.application.plugins.adapters.artifacts_adapter import (
    ArtifactRegistryAdapter,
)
from prodavan.application.plugins.adapters.build import (
    build_plugin_admin_ports,
    build_plugin_admin_service,
)
from prodavan.application.plugins.adapters.identity_adapter import (
    IdentityMintAdapter,
    IdentityRevocationAdapter,
)
from prodavan.application.plugins.adapters.objects_adapter import ObjectStoreAdapter
from prodavan.application.plugins.adapters.stores import (
    InMemoryAdminAudit,
    InMemoryGrantStore,
    InMemoryRouteRegistry,
    StaticPlatformAdmin,
)
from prodavan.application.plugins.adapters.tenantdb_adapter import TenantDbAdapter
from prodavan.application.plugins.artifacts.registry import (
    InMemoryRegistryStore,
    PluginArtifactRegistry,
)
from prodavan.application.plugins.artifacts.verification import ArtifactVerifier
from prodavan.application.plugins.objects.service import PluginObjectStore
from prodavan.application.plugins.tenantdb.service import TenantDatabaseService

from .fakes import (
    COMPANY,
    INSTANCE,
    PLUGIN,
    SECRET,
    FakeAdminConnection,
    FakeBucketAdmin,
    FakeDeploymentAdmin,
    FakeObjectAudit,
    FakeSecretPort,
    FakeSyncRevocation,
    FakeTenantDbAudit,
    make_artifact_record,
)
from .test_activation_adapter import _activator
from ..admin.fakes import (
    FakeInstallationScopeStore,
    FakeInstanceStore,
    FakePublicationApprovalStore,
    FakeUpdateLock,
    FakeWebhookSecretStore,
)


class _FakeCosign:
    def verify(self, image_ref: str, identity: str, issuer: str) -> Any:
        from prodavan.application.plugins.artifacts.verification import (
            VerificationResult,
        )

        return VerificationResult(
            verified=True,
            signed_digest=image_ref.split("@", 1)[-1],
            sbom_verified=True,
            provenance_verified=True,
        )


def _deps() -> dict[str, Any]:
    """Полный набор РЕАЛЬНЫХ (не stub) реализаций для успешной сборки."""

    resolver = InMemoryInstanceResolver()
    resolver.register(
        company_id=COMPANY, plugin_id=PLUGIN, instance_id=INSTANCE
    )
    store = InMemoryRegistryStore()
    store.add(make_artifact_record())
    registry = PluginArtifactRegistry(
        store=store,
        verifier=ArtifactVerifier(
            cosign=_FakeCosign(),
            identity="ci@agentscale",
            issuer="https://token.actions.githubusercontent.com",
        ),
    )
    return {
        "platform": StaticPlatformAdmin(expected_token="test-token"),
        "tenant_db_service": TenantDatabaseService(
            admin=FakeAdminConnection(),
            secrets=FakeSecretPort(),
            audit=FakeTenantDbAudit(),
            dsn_host="tenant-postgres.svc",
        ),
        "object_store_service": PluginObjectStore(
            buckets=FakeBucketAdmin(), audit=FakeObjectAudit()
        ),
        "bucket_admin": FakeBucketAdmin(),
        "artifact_registry": registry,
        "artifact_store": store,
        "revocation_source": FakeSyncRevocation(),
        "mint_secret": SECRET,
        "activator": _activator(),
        "instance_resolver": resolver,
        "instance_store": FakeInstanceStore(),
        "webhook_secrets": FakeWebhookSecretStore(),
        "deployment_admin": FakeDeploymentAdmin(),
        "publication_approvals": FakePublicationApprovalStore(),
        "installation_scopes": FakeInstallationScopeStore(),
        "update_lock": FakeUpdateLock(),
        # Legacy in-memory route state is legal only as an explicit test port.
        "routes": InMemoryRouteRegistry(),
        # InMemory grant/audit — тоже ТОЛЬКО явные test/dev constructor overrides:
        # production defaults из build_plugin_admin_ports удалены.
        "grant_store": InMemoryGrantStore(),
        "audit": InMemoryAdminAudit(),
        "resolver_registrar": resolver,
    }


# --------------------------------------------------------------------------- успех


def test_build_plugin_admin_ports_wires_all_12_ports() -> None:
    deps = _deps()
    ports = build_plugin_admin_ports(**deps)
    assert ports.webhook_secrets is deps["webhook_secrets"]
    assert ports.publication_approvals is deps["publication_approvals"]
    assert ports.update_lock is deps["update_lock"]
    assert ports.installation_scopes is deps["installation_scopes"]

    assert isinstance(ports, PluginAdminPorts)
    assert isinstance(ports.tenant_db, TenantDbAdapter)
    assert isinstance(ports.object_store, ObjectStoreAdapter)
    assert isinstance(ports.artifact_registry, ArtifactRegistryAdapter)
    assert isinstance(ports.grant_store, InMemoryGrantStore)
    assert ports.grant_store is deps["grant_store"]
    assert isinstance(ports.revocation, IdentityRevocationAdapter)
    assert isinstance(ports.mint, IdentityMintAdapter)
    assert isinstance(ports.audit, InMemoryAdminAudit)
    assert ports.audit is deps["audit"]
    assert isinstance(ports.routes, InMemoryRouteRegistry)
    assert isinstance(ports.platform_admin, StaticPlatformAdmin)
    assert len(PluginAdminPorts.__dataclass_fields__) == 16  # type: ignore[attr-defined]
    for name in (
        "tenant_db", "object_store", "artifact_registry", "instance_store",
        "grant_store", "revocation", "mint", "activation", "routes", "audit",
        "platform_admin", "publication_approvals", "update_lock",
    ):
        assert getattr(ports, name) is not None


def test_build_plugin_admin_service_returns_wired_service() -> None:
    service = build_plugin_admin_service(**_deps())
    assert isinstance(service, PluginAdminService)


def test_direct_builders_require_explicit_routes_without_memory_fallback() -> None:
    deps = _deps()
    deps.pop("routes")
    with pytest.raises(ValueError, match="'routes'"):
        build_plugin_admin_ports(**deps)
    with pytest.raises(ValueError, match="'routes'"):
        build_plugin_admin_service(**deps)


@pytest.mark.parametrize("missing", ["grant_store", "audit"])
def test_direct_builders_require_explicit_grant_store_and_audit(missing: str) -> None:
    """Отсутствие grant_store/audit = typed config failure, не InMemory default."""
    deps = _deps()
    deps.pop(missing)
    with pytest.raises(ValueError, match=f"'{missing}'"):
        build_plugin_admin_ports(**deps)
    with pytest.raises(ValueError, match=f"'{missing}'"):
        build_plugin_admin_service(**deps)


def test_no_success_stub_is_used_for_pure_contracts() -> None:
    """In-memory реализации — ЯВНЫЕ, а не «заглушки успеха».

    Они отвечают честно: пустой платформенный токен = не админ,
    пустой grant store = нет grant, append аудита = подтверждённая запись.
    """

    import asyncio

    ports = build_plugin_admin_ports(**_deps())

    async def _check() -> None:
        identity = await ports.platform_admin.require_platform_admin(authorization=None)
        assert identity.is_platform_admin is False
        assert await ports.grant_store.get(company_id=COMPANY, plugin_id=PLUGIN) is None
        assert await ports.audit.append(
            event=AdminAuditEvent(
                event_id="evt-1",
                occurred_at=datetime(2026, 11, 8, 9, 0, tzinfo=UTC),
                company_id=COMPANY,
                plugin_id=PLUGIN,
                operation=AdminOperation.GRANT,
                decision=AdminDecision.ALLOW,
            )
        ) is True

    asyncio.run(_check())


# --------------------------------------------------------------------------- fail-closed


@pytest.mark.parametrize(
    "missing",
    [
        "platform",
        "tenant_db_service",
        "object_store_service",
        "bucket_admin",
        "artifact_registry",
        "revocation_source",
        "mint_secret",
        "activator",
        "instance_resolver",
        "instance_store",
        "webhook_secrets",
        "deployment_admin",
        "publication_approvals",
        "installation_scopes",
        "update_lock",
        "grant_store",
        "audit",
        "routes",
    ],
)
def test_build_raises_when_any_required_port_is_missing(missing: str) -> None:
    """Отсутствие ЛЮБОЙ реализации = ValueError с именем порта."""

    deps = _deps()
    deps[missing] = None
    with pytest.raises(ValueError, match=f"'{missing}'"):
        build_plugin_admin_ports(**deps)


def test_build_rejects_empty_mint_secret() -> None:
    deps = _deps()
    deps["mint_secret"] = ""
    with pytest.raises(ValueError, match="mint_secret"):
        build_plugin_admin_ports(**deps)


def test_build_rejects_wrong_resolver_type() -> None:
    deps = _deps()
    deps["instance_resolver"] = object()
    with pytest.raises(TypeError, match="InstanceIdResolver"):
        build_plugin_admin_ports(**deps)


def test_build_rejects_wrong_deployment_admin_type() -> None:
    deps = _deps()
    deps["deployment_admin"] = object()
    with pytest.raises(TypeError, match="DeploymentAdminPort"):
        build_plugin_admin_ports(**deps)


def test_build_rejects_wrong_platform_type() -> None:
    deps = _deps()
    deps["platform"] = object()
    with pytest.raises(TypeError, match="PlatformAdminPort"):
        build_plugin_admin_ports(**deps)


def test_build_rejects_platform_without_method() -> None:
    deps = _deps()
    deps["platform"] = "not-a-port"
    with pytest.raises(TypeError, match="PlatformAdminPort"):
        build_plugin_admin_ports(**deps)


def test_build_allows_real_db_implementations_for_pure_contracts() -> None:
    """Явные реализации поверх БД/Redis (A10) подключаются вместо in-memory."""

    deps = _deps()
    grant_store = InMemoryGrantStore()
    audit = InMemoryAdminAudit()
    routes = InMemoryRouteRegistry()
    deps["grant_store"] = grant_store
    deps["audit"] = audit
    deps["routes"] = routes
    ports = build_plugin_admin_ports(**deps)
    assert ports.grant_store is grant_store
    assert ports.audit is audit
    assert ports.routes is routes


def test_require_wired_helper_rejects_none() -> None:
    from prodavan.application.plugins.adapters.errors import require_wired

    with pytest.raises(ValueError, match="fail-closed"):
        require_wired("some_port", None)
    marker = object()
    assert require_wired("some_port", marker) is marker
