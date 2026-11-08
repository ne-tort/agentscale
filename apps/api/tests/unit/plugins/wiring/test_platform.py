"""F11: ``build_plugin_platform`` — выключено/включено, цепочка, инъекция портов.

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ (verification freeze).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pytest
from jwt import get_unverified_header

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
from tests.unit.plugins.wiring.conftest import (
    TEST_DIGEST,
    TEST_OLD_SECRET_1,
    TEST_SECRET,
    FakeCosign,
    FakeGrantStore,
    FakeSettings,
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
    cosign: FakeCosign,
    admin_ports: object,
) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    with pytest.raises(PluginPlatformConfigError, match="grant_store"):
        build_plugin_platform(
            FakeSettings(), cosign=cosign, admin_ports=admin_ports
        )


def test_enabled_without_cosign_raises(
    monkeypatch: pytest.MonkeyPatch,
    grant_store: FakeGrantStore,
    admin_ports: object,
) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    with pytest.raises(PluginPlatformConfigError, match="cosign"):
        build_plugin_platform(
            FakeSettings(), grant_store=grant_store, admin_ports=admin_ports
        )


def test_enabled_without_admin_ports_raises(
    monkeypatch: pytest.MonkeyPatch,
    grant_store: FakeGrantStore,
    cosign: FakeCosign,
) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    with pytest.raises(PluginPlatformConfigError, match="admin_ports"):
        build_plugin_platform(
            FakeSettings(), grant_store=grant_store, cosign=cosign
        )


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


def test_platform_report_lists_stub_adapters(
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
    assert report["adapters"]["stub_count"] == 9
    assert len(report["adapters"]["requires_f11_work"]) == 9
    assert report["adapters"]["db_direct_registered"] is False
    # Отчёт не должен содержать секрет подписи.
    assert TEST_SECRET not in repr(report)


# --------------------------------------------------------------------------- цепочка verify -> check -> surface


def test_verify_check_surface_chain(
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

    token = mint_plugin_claims(grant=FakeGrant(), instance=FakeInstance(), secret=TEST_SECRET)
    assert get_unverified_header(token)["kid"] == platform.keyring.active_kid

    # 1) verify: подпись + iss/aud/typ + claim + exp.
    claims = verify_plugin_token(token, keyring=platform.keyring)
    assert claims.grant_id == "grant_01"
    assert claims.typ == "plugin_capability"

    # 2) check: grant_id активен в БД (A10).
    assert platform.revocation.check(claims) is claims
    assert platform.revocation.is_active("grant_01", now=None) is True

    # 3) assert_plugin_surface: только /capabilities/v1/*.
    assert assert_plugin_surface(claims, "/capabilities/v1/cache/get") is claims
    with pytest.raises(PluginTokenError) as ei:
        assert_plugin_surface(claims, "/internal/pods/pod_1/workspace-archive")
    assert ei.value.code == PluginTokenErrorCode.FORBIDDEN_SURFACE
    with pytest.raises(PluginTokenError):
        assert_plugin_surface(claims, "/agent/events")


def test_chain_step_two_rejects_revoked_grant(
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

    platform.revocation.revoke("grant_01")
    with pytest.raises(PluginTokenError) as ei:
        platform.revocation.check(claims)
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
