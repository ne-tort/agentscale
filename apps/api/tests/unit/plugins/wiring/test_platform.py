"""F11: ``build_plugin_platform`` — выключено/включено, цепочка, инъекция портов.

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ (verification freeze).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pytest
from jwt import get_unverified_header

from prodavan.application.plugins.admin.ports import PluginAdminPorts
from prodavan.application.plugins.admin.service import PluginAdminService
from prodavan.application.plugins.artifacts.registry import InMemoryRegistryStore
from prodavan.application.plugins.identity.allowlist import assert_plugin_surface
from prodavan.application.plugins.identity.mint import mint_plugin_claims, verify_plugin_token
from prodavan.application.plugins.identity.types import (
    PluginTokenError,
    PluginTokenErrorCode,
)
from prodavan.application.plugins.wiring.config import (
    PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV,
    PluginPlatformConfig,
    PluginPlatformConfigError,
)
from prodavan.application.plugins.wiring.platform import (
    PluginPlatform,
    build_plugin_platform,
)
from prodavan.application.plugins.wiring.revocation import DurableGrantRevocation
from prodavan.domain.errors import AppError
from tests.unit.plugins.admin.fakes import Harness
from .conftest import (
    TEST_CAPABILITIES,
    TEST_COMPANY_ID,
    TEST_DIGEST,
    TEST_MANIFEST_VERSION,
    TEST_PLUGIN_ID,
    TEST_OLD_SECRET_1,
    TEST_SECRET,
    FakeCosign,
    FakeGrantStore,
    FakeSecretDeliveryControl,
    FakeSettings,
    fake_capability_overrides,
    required_platform_port_overrides,
)


@dataclass(frozen=True)
class FakeGrant:
    grant_id: str = "grant_01"
    plugin_id: str = "mod_equipment"
    company_id: str = "co_123"
    scopes: tuple[str, ...] = ("cache", "objects")
    manifest_version: str = "1.2.0"
    artifact_digest: str = TEST_DIGEST
    quota_ref: str = "co_123/plan_pro"


@dataclass(frozen=True)
class FakeInstance:
    plugin_instance_id: str = "inst_abc"
    cabinet_id: str | None = None
    project_id: str | None = None


def _full_overrides(
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> dict[str, object]:
    """Минимально достаточный набор портов для УСПЕШНОЙ сборки."""
    return {
        "grant_store": grant_store,
        "cosign": cosign,
        "admin_ports": admin_ports,
        "artifact_registry_store": InMemoryRegistryStore(),
        # Обязательные audit/quota/activation_state_store — ЯВНЫЕ test doubles.
        **required_platform_port_overrides(),
        **fake_capability_overrides(),
    }


# --------------------------------------------------------------------------- enabled=False


def test_disabled_platform_returns_none_without_building_services(
    monkeypatch: pytest.MonkeyPatch,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    settings = FakeSettings(plugin_platform_enabled=False)

    platform = build_plugin_platform(settings, **_full_overrides(grant_store, cosign, admin_ports))

    assert platform is None
    # Ни одного обращения к портам: сервисы НЕ создавались.
    assert grant_store.get_calls == []
    assert cosign.calls == []


def test_disabled_platform_does_not_require_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    settings = FakeSettings(
        plugin_platform_enabled=False,
        plugin_claims_secret="",
        plugin_static_origin="",
        plugin_shell_origin="",
    )
    # Ни одного override: если бы платформа начала собираться, упала бы иначе.
    assert build_plugin_platform(settings) is None


def test_enabled_without_secret_raises_build_error(
    monkeypatch: pytest.MonkeyPatch,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    settings = FakeSettings(plugin_platform_enabled=True, plugin_claims_secret="")
    with pytest.raises(PluginPlatformConfigError) as ei:
        build_plugin_platform(settings, **_full_overrides(grant_store, cosign, admin_ports))
    assert "PLUGIN_CLAIMS_SECRET" in str(ei.value)


def test_enabled_without_static_origin_raises(
    monkeypatch: pytest.MonkeyPatch,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    settings = FakeSettings(plugin_platform_enabled=True, plugin_static_origin="")
    with pytest.raises(PluginPlatformConfigError, match="PLUGIN_STATIC_ORIGIN"):
        build_plugin_platform(settings, **_full_overrides(grant_store, cosign, admin_ports))


# --------------------------------------------------------------------------- обязательные порты


def test_enabled_without_grant_store_raises(
    monkeypatch: pytest.MonkeyPatch,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    overrides = _full_overrides(grant_store, cosign, admin_ports)
    overrides.pop("grant_store")
    with pytest.raises(PluginPlatformConfigError, match="grant_store"):
        build_plugin_platform(FakeSettings(), **overrides)


def test_enabled_without_cosign_raises(
    monkeypatch: pytest.MonkeyPatch,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    overrides = _full_overrides(grant_store, cosign, admin_ports)
    overrides.pop("cosign")
    with pytest.raises(PluginPlatformConfigError, match="cosign"):
        build_plugin_platform(FakeSettings(), **overrides)


def test_enabled_without_artifact_registry_store_raises(
    monkeypatch: pytest.MonkeyPatch,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    overrides = _full_overrides(grant_store, cosign, admin_ports)
    overrides.pop("artifact_registry_store")
    with pytest.raises(PluginPlatformConfigError, match="artifact_registry_store"):
        build_plugin_platform(FakeSettings(), **overrides)


def test_enabled_without_admin_ports_raises(
    monkeypatch: pytest.MonkeyPatch,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    overrides = _full_overrides(grant_store, cosign, admin_ports)
    overrides.pop("admin_ports")
    with pytest.raises(PluginPlatformConfigError, match="admin_ports"):
        build_plugin_platform(FakeSettings(), **overrides)


@pytest.mark.parametrize("port", ["audit", "quota", "activation_state_store"])
def test_enabled_without_required_durable_port_raises(
    monkeypatch: pytest.MonkeyPatch,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
    port: str,
) -> None:
    """Отсутствие audit/quota/state_store = typed config failure, НЕ InMemory default."""
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    overrides = _full_overrides(grant_store, cosign, admin_ports)
    overrides.pop(port)
    with pytest.raises(PluginPlatformConfigError, match=port):
        build_plugin_platform(FakeSettings(), **overrides)


def test_explicit_ports_are_shared_without_in_memory_duplicates(
    monkeypatch: pytest.MonkeyPatch,
    fake_settings: FakeSettings,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    """Сервисы используют РОВНО переданные порты: второй store не создаётся."""
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    overrides = _full_overrides(grant_store, cosign, admin_ports)
    platform = build_plugin_platform(fake_settings, **overrides)
    assert platform is not None
    service = platform.capability_service
    assert service._grant_store is grant_store
    assert service._audit is overrides["audit"]
    assert service._quota is overrides["quota"]
    assert platform.activator._store is overrides["activation_state_store"]
    assert platform.admin._ports is admin_ports
    assert platform.admin._ports.update_lock is admin_ports.update_lock


def test_full_service_overrides_skip_default_port_construction(
    monkeypatch: pytest.MonkeyPatch,
    fake_settings: FakeSettings,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    """Полный service override: default-конструктор не вызывается, порты не нужны."""
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    overrides = _full_overrides(grant_store, cosign, admin_ports)
    for port in ("audit", "quota", "activation_state_store"):
        overrides.pop(port)
    service = object()
    activator = object()
    platform = build_plugin_platform(
        fake_settings,
        capability_service=service,  # type: ignore[arg-type]
        activator=activator,  # type: ignore[arg-type]
        **overrides,
    )
    assert platform is not None
    assert platform.capability_service is service
    assert platform.activator is activator


class _RevocationOverride:
    """Полный revocation service override: default _build_revocation НЕ вызывается."""

    async def is_active(self, grant_id: str, *, now: object = None) -> bool:
        return True

    async def revoke(self, grant_id: str) -> bool:
        return True

    async def is_revoked(self, grant_id: str) -> bool:
        return False

    async def check(self, claims: object) -> object:
        return claims


def test_revocation_override_does_not_replace_required_grant_store(
    monkeypatch: pytest.MonkeyPatch,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    """Regression: revocation override + default capability_service БЕЗ grant_store = config error."""
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    overrides = _full_overrides(grant_store, cosign, admin_ports)
    overrides.pop("grant_store")
    overrides["revocation"] = _RevocationOverride()
    with pytest.raises(PluginPlatformConfigError, match="grant_store"):
        build_plugin_platform(FakeSettings(), **overrides)


def test_none_grant_store_with_revocation_override_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    """Явный grant_store=None + revocation override = typed failure, НЕ grant_store=None в ядре."""
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    overrides = _full_overrides(grant_store, cosign, admin_ports)
    overrides["grant_store"] = None
    overrides["revocation"] = _RevocationOverride()
    with pytest.raises(PluginPlatformConfigError, match="grant_store"):
        build_plugin_platform(FakeSettings(), **overrides)


def test_revocation_override_shares_explicit_grant_store_without_duplicates(
    monkeypatch: pytest.MonkeyPatch,
    fake_settings: FakeSettings,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    """Revocation override + явный grant_store: ядро использует РОВНО тот же store."""
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    revocation = _RevocationOverride()
    overrides = _full_overrides(grant_store, cosign, admin_ports)
    overrides["revocation"] = revocation
    platform = build_plugin_platform(fake_settings, **overrides)
    assert platform is not None
    assert platform.revocation is revocation
    assert platform.capability_service._grant_store is grant_store


def test_full_capability_service_override_skips_grant_store_requirement(
    monkeypatch: pytest.MonkeyPatch,
    fake_settings: FakeSettings,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    """Полный capability_service override: default factory не вызывается, grant_store не нужен."""
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    overrides = _full_overrides(grant_store, cosign, admin_ports)
    overrides.pop("grant_store")
    overrides["revocation"] = _RevocationOverride()
    sentinel = object()
    platform = build_plugin_platform(
        fake_settings,
        capability_service=sentinel,  # type: ignore[arg-type]
        **overrides,
    )
    assert platform is not None
    assert platform.capability_service is sentinel
    assert platform.revocation is overrides["revocation"]


def test_enabled_without_cosign_identity_raises(
    monkeypatch: pytest.MonkeyPatch,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    with pytest.raises(PluginPlatformConfigError, match="PLUGIN_COSIGN_IDENTITY"):
        build_plugin_platform(
            FakeSettings(plugin_cosign_identity=""),
            **_full_overrides(grant_store, cosign, admin_ports),
        )


def test_unknown_override_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    with pytest.raises(PluginPlatformConfigError, match="unknown"):
        build_plugin_platform(FakeSettings(), secret=TEST_SECRET)


# --------------------------------------------------------------------------- успешная сборка


@pytest.mark.asyncio
async def test_admin_ports_exact_approval_grant_and_revoke_secret_lifecycle(
    admin_ports: PluginAdminPorts,
    admin_harness: Harness,
) -> None:
    approval = await admin_harness.publication_approvals.get(
        company_id=TEST_COMPANY_ID,
        plugin_id=TEST_PLUGIN_ID,
        manifest_version=TEST_MANIFEST_VERSION,
        artifact_digest=TEST_DIGEST,
    )
    assert approval is not None
    assert approval.consumed_at is None
    assert approval.capability_set == TEST_CAPABILITIES
    delivery = admin_ports.secret_delivery
    assert isinstance(delivery, FakeSecretDeliveryControl)

    admin = PluginAdminService(admin_ports)
    try:
        receipt = await admin.grant(
            company_id=TEST_COMPANY_ID,
            plugin_id=TEST_PLUGIN_ID,
            manifest_version=TEST_MANIFEST_VERSION,
            artifact_digest=TEST_DIGEST,
            capabilities=TEST_CAPABILITIES,
        )
    except AppError as exc:
        assert exc.code != "PUBLICATION_APPROVAL_REQUIRED"
        raise

    assert receipt.grant.is_active
    consumed = await admin_harness.publication_approvals.get(
        company_id=TEST_COMPANY_ID,
        plugin_id=TEST_PLUGIN_ID,
        manifest_version=TEST_MANIFEST_VERSION,
        artifact_digest=TEST_DIGEST,
    )
    assert consumed is not None and consumed.consumed_at is not None
    assert len(admin_harness.webhook_secrets.records) == 1
    assert len(delivery.deliver_requests) == 1

    await admin.revoke(company_id=TEST_COMPANY_ID, plugin_id=TEST_PLUGIN_ID)

    assert admin_harness.webhook_secrets.records == {}
    assert len(delivery.revoke_requests) == 1
    assert delivery.revoke_requests[0].request_id.startswith("revoke:")


@pytest.mark.asyncio
async def test_admin_ports_missing_approval_remains_fail_closed(
    admin_ports: PluginAdminPorts,
    admin_harness: Harness,
) -> None:
    admin_harness.publication_approvals.records.clear()
    delivery = admin_ports.secret_delivery
    assert isinstance(delivery, FakeSecretDeliveryControl)

    with pytest.raises(AppError) as caught:
        await PluginAdminService(admin_ports).grant(
            company_id=TEST_COMPANY_ID,
            plugin_id=TEST_PLUGIN_ID,
            manifest_version=TEST_MANIFEST_VERSION,
            artifact_digest=TEST_DIGEST,
            capabilities=TEST_CAPABILITIES,
        )

    assert caught.value.code == "PUBLICATION_APPROVAL_REQUIRED"
    assert admin_harness.grant_store.grants == {}
    assert admin_harness.webhook_secrets.records == {}
    assert admin_harness.activation.deployments == {}
    assert admin_harness.mint.counter == 0
    assert delivery.deliver_requests == []


def test_build_platform_has_every_required_field(
    monkeypatch: pytest.MonkeyPatch,
    fake_settings: FakeSettings,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    platform = build_plugin_platform(
        fake_settings, **_full_overrides(grant_store, cosign, admin_ports)
    )

    assert isinstance(platform, PluginPlatform)
    assert platform.enabled is True
    assert isinstance(platform.revocation, DurableGrantRevocation)
    assert platform.config.claims_secret == TEST_SECRET
    for name in (
        "config",
        "keyring",
        "revocation",
        "capability_service",
        "artifact_verifier",
        "artifact_registry",
        "registry",
        "tenant_db",
        "object_store",
        "egress",
        "activator",
        "gateway",
        "admin",
        "event_dispatcher",
    ):
        assert getattr(platform, name) is not None, name


def test_platform_report_has_no_stub_adapters(
    monkeypatch: pytest.MonkeyPatch,
    fake_settings: FakeSettings,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    platform = build_plugin_platform(
        fake_settings, **_full_overrides(grant_store, cosign, admin_ports)
    )
    assert platform is not None
    report = platform.report()
    assert report["adapters"]["stub_count"] == 0
    assert len(report["adapters"]["requires_f11_work"]) == 0
    assert report["adapters"]["db_direct_registered"] is False
    # Отчёт не должен содержать секрет подписи.
    assert TEST_SECRET not in repr(report)


# --------------------------------------------------------------------------- цепочка verify -> check -> surface


@pytest.mark.asyncio
async def test_verify_check_surface_chain(
    monkeypatch: pytest.MonkeyPatch,
    fake_settings: FakeSettings,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    """Ключевая цепочка v4 §4.1/§4.2 (критерии №12, №17)."""
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    platform = build_plugin_platform(
        fake_settings, **_full_overrides(grant_store, cosign, admin_ports)
    )
    assert platform is not None
    assert platform.capability_service._grant_store is grant_store

    token = mint_plugin_claims(grant=FakeGrant(), instance=FakeInstance(), secret=TEST_SECRET)
    assert get_unverified_header(token)["kid"] == platform.keyring.active_kid

    # 1) verify: подпись + iss/aud/typ + claim + exp.
    claims = verify_plugin_token(token, keyring=platform.keyring)
    assert claims.grant_id == "grant_01"
    assert claims.typ == "plugin_capability"

    # 2) check: grant_id активен в БД (A10).
    assert await platform.revocation.check(claims) is claims
    assert await platform.revocation.is_active("grant_01", now=None) is True

    # 3) assert_plugin_surface: только /capabilities/v1/*.
    assert assert_plugin_surface(claims, "/capabilities/v1/cache/get") is claims
    with pytest.raises(PluginTokenError) as ei:
        assert_plugin_surface(claims, "/internal/pods/pod_1/workspace-archive")
    assert ei.value.code == PluginTokenErrorCode.FORBIDDEN_SURFACE
    with pytest.raises(PluginTokenError):
        assert_plugin_surface(claims, "/agent/events")


@pytest.mark.asyncio
async def test_chain_step_two_rejects_revoked_grant(
    monkeypatch: pytest.MonkeyPatch,
    fake_settings: FakeSettings,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    """Критерий №12: отзыв => старый (ещё не истёкший) токен отклоняется."""
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    platform = build_plugin_platform(
        fake_settings, **_full_overrides(grant_store, cosign, admin_ports)
    )
    assert platform is not None
    token = mint_plugin_claims(grant=FakeGrant(), instance=FakeInstance(), secret=TEST_SECRET)
    claims = verify_plugin_token(token, keyring=platform.keyring)

    await platform.revocation.revoke("grant_01")
    with pytest.raises(PluginTokenError) as ei:
        await platform.revocation.check(claims)
    assert ei.value.code == PluginTokenErrorCode.REVOKED
    # verify всё ещё проходит (подпись/exp валидны) — отказ именно на check.
    assert verify_plugin_token(token, keyring=platform.keyring).grant_id == "grant_01"


def test_chain_docstring_documents_all_three_steps() -> None:
    from prodavan.application.plugins.wiring import platform as module

    doc = module.__doc__ or ""
    assert "verify" in doc
    assert "check" in doc
    assert "assert_plugin_surface" in doc
    assert "bump_pod_bridge_generation" in doc  # явно запрещено в docstring


def test_platform_keyring_supports_rotation(
    monkeypatch: pytest.MonkeyPatch,
    fake_settings: FakeSettings,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    monkeypatch.setenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, TEST_OLD_SECRET_1)
    platform = build_plugin_platform(
        fake_settings, **_full_overrides(grant_store, cosign, admin_ports)
    )
    assert platform is not None
    old_token = mint_plugin_claims(
        grant=FakeGrant(), instance=FakeInstance(), secret=TEST_OLD_SECRET_1
    )
    claims = verify_plugin_token(old_token, keyring=platform.keyring, now=int(time.time()))
    assert claims.grant_id == "grant_01"


def test_config_override_allows_tests_without_settings(
    enabled_config: PluginPlatformConfig,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    platform = build_plugin_platform(
        object(),  # settings не читается: config передан явно
        config=enabled_config,
        **_full_overrides(grant_store, cosign, admin_ports),
    )
    assert isinstance(platform, PluginPlatform)
    assert platform.config is enabled_config


def test_service_overrides_are_used(
    monkeypatch: pytest.MonkeyPatch,
    fake_settings: FakeSettings,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    """Все зависимости — через инъекцию портов/сервисов (тесты подменяют fakes)."""
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    sentinel = object()
    platform = build_plugin_platform(
        fake_settings,
        capability_service=sentinel,  # type: ignore[arg-type]
        **_full_overrides(grant_store, cosign, admin_ports),
    )
    assert platform is not None
    assert platform.capability_service is sentinel
