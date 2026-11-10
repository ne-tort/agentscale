"""F11: ``PluginPlatformConfig`` — все PLUGIN_* поля, дубль guard'ов, disabled.

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ (verification freeze).
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.wiring.config import (
    MAX_CLAIMS_TTL_SECONDS,
    MIN_CLAIMS_TTL_SECONDS,
    PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV,
    PLUGIN_SETTING_FIELDS,
    PluginPlatformConfig,
    PluginPlatformConfigError,
    read_previous_secrets,
)
from tests.unit.plugins.wiring.conftest import (
    TEST_OLD_SECRET_1,
    TEST_OLD_SECRET_2,
    TEST_SECRET,
    TEST_STATIC_ORIGIN,
    TEST_SHELL_ORIGIN,
    FakeSettings,
)


def test_from_settings_maps_all_plugin_fields(fake_settings: FakeSettings) -> None:
    config = PluginPlatformConfig.from_settings(fake_settings)

    assert config.enabled is True
    assert config.claims_secret == TEST_SECRET
    assert config.event_signing_secret_ref == fake_settings.plugin_event_signing_secret_ref
    assert config.claims_ttl_seconds == fake_settings.plugin_claims_ttl_seconds
    assert config.claims_issuer == fake_settings.plugin_claims_issuer
    assert config.claims_audience == fake_settings.plugin_claims_audience
    assert config.static_origin == TEST_STATIC_ORIGIN
    assert config.shell_origin == TEST_SHELL_ORIGIN
    assert config.tenant_db_admin_url == fake_settings.plugin_tenant_db_admin_url
    assert config.tenant_db_host == fake_settings.plugin_tenant_db_host
    assert config.tenant_db_port == fake_settings.plugin_tenant_db_port
    assert config.egress_mode == fake_settings.plugin_egress_mode
    assert config.egress_approved is False
    assert config.idle_ttl_seconds == fake_settings.plugin_idle_ttl_seconds
    assert config.ready_timeout_seconds == fake_settings.plugin_ready_timeout_seconds
    assert config.max_concurrent_wakes == fake_settings.plugin_max_concurrent_wakes
    assert config.oci_registry == fake_settings.plugin_oci_registry
    assert config.cosign_identity == fake_settings.plugin_cosign_identity
    assert config.cosign_issuer == fake_settings.plugin_cosign_issuer
    assert config.min_platform_version == fake_settings.plugin_min_platform_version


def test_every_settings_field_is_projected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Новое PLUGIN_* поле не должно теряться молча."""
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    settings = FakeSettings()
    for name in PLUGIN_SETTING_FIELDS:
        assert hasattr(settings, name), f"Fixture FakeSettings missing {name}"
    assert len(PLUGIN_SETTING_FIELDS) == 20


def test_from_settings_requires_every_field() -> None:
    class Partial:
        plugin_platform_enabled = True
        plugin_claims_secret = TEST_SECRET

    with pytest.raises(PluginPlatformConfigError) as ei:
        PluginPlatformConfig.from_settings(Partial())
    assert "missing required PLUGIN_* field" in str(ei.value)


def test_enabled_false_config_is_valid_without_secret_or_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    config = PluginPlatformConfig.from_settings(
        FakeSettings(
            plugin_platform_enabled=False,
            plugin_claims_secret="",
            plugin_event_signing_secret_ref="",
            plugin_static_origin="",
            plugin_shell_origin="",
        )
    )
    assert config.enabled is False
    # Но безопасные инварианты всё равно проверяются.
    assert config.claims_ttl_seconds == 300


def test_enabled_without_secret_fails_at_build_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Дубль guard'а settings: enabled без секрета = ошибка СБОРКИ."""
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    with pytest.raises(PluginPlatformConfigError) as ei:
        PluginPlatformConfig.from_settings(
            FakeSettings(plugin_platform_enabled=True, plugin_claims_secret="")
        )
    assert "PLUGIN_CLAIMS_SECRET" in str(ei.value)
    assert "AUTH_TEST_SECRET" in str(ei.value)  # явный запрет dev-фолбэка


def test_enabled_without_static_origin_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    with pytest.raises(PluginPlatformConfigError) as ei:
        PluginPlatformConfig.from_settings(
            FakeSettings(plugin_platform_enabled=True, plugin_static_origin="")
        )
    assert "PLUGIN_STATIC_ORIGIN" in str(ei.value)


def test_enabled_without_shell_origin_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    with pytest.raises(PluginPlatformConfigError, match="PLUGIN_SHELL_ORIGIN"):
        PluginPlatformConfig.from_settings(
            FakeSettings(plugin_platform_enabled=True, plugin_shell_origin="")
        )


@pytest.mark.parametrize(
    "shell_origin",
    [
        "http://agentscale.example.test",
        "https://*.example.test",
        "https://agentscale.example.test/path",
        "https://agentscale.example.test/?origin=x",
        "https://user:pass@agentscale.example.test",
    ],
)
def test_enabled_rejects_non_exact_shell_origin(
    monkeypatch: pytest.MonkeyPatch, shell_origin: str
) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    with pytest.raises(PluginPlatformConfigError, match="PLUGIN_SHELL_ORIGIN"):
        PluginPlatformConfig.from_settings(
            FakeSettings(plugin_platform_enabled=True, plugin_shell_origin=shell_origin)
        )


def test_shell_origin_must_be_distinct_from_static(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    with pytest.raises(PluginPlatformConfigError, match="distinct"):
        PluginPlatformConfig.from_settings(
            FakeSettings(
                plugin_platform_enabled=True,
                plugin_shell_origin=TEST_STATIC_ORIGIN,
            )
        )


def test_enabled_without_cosign_identity_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Дополнительный guard F11: keyless cosign без identity = тихий карантин."""
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    with pytest.raises(PluginPlatformConfigError, match="PLUGIN_COSIGN_IDENTITY"):
        PluginPlatformConfig.from_settings(
            FakeSettings(plugin_platform_enabled=True, plugin_cosign_identity="")
        )


@pytest.mark.parametrize("ttl", [0, 59, 901, 10000])
def test_ttl_out_of_range_fails(monkeypatch: pytest.MonkeyPatch, ttl: int) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    with pytest.raises(PluginPlatformConfigError) as ei:
        PluginPlatformConfig.from_settings(
            FakeSettings(plugin_platform_enabled=False, plugin_claims_ttl_seconds=ttl)
        )
    assert f"{MIN_CLAIMS_TTL_SECONDS}..{MAX_CLAIMS_TTL_SECONDS}" in str(ei.value)


def test_invalid_egress_mode_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    with pytest.raises(PluginPlatformConfigError, match="PLUGIN_EGRESS_MODE"):
        PluginPlatformConfig.from_settings(
            FakeSettings(plugin_platform_enabled=False, plugin_egress_mode="allow-all")
        )


def test_full_egress_requires_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, raising=False)
    with pytest.raises(PluginPlatformConfigError, match="PLUGIN_EGRESS_APPROVED"):
        PluginPlatformConfig.from_settings(
            FakeSettings(
                plugin_platform_enabled=False,
                plugin_egress_mode="full",
                plugin_egress_approved=False,
            )
        )


def test_enabled_without_event_signing_ref_fails() -> None:
    with pytest.raises(PluginPlatformConfigError, match="PLUGIN_EVENT_SIGNING_SECRET_REF"):
        PluginPlatformConfig.from_settings(
            FakeSettings(plugin_event_signing_secret_ref="")
        )


@pytest.mark.parametrize(
    "ref",
    [
        "plaintext-secret",
        " vault://unit/event-key",
        "k8s-secret://agentscale-plugin-events",
        "k8s-secret://agentscale-plugin-events/signing-key/extra",
        "k8s-secret://UPPERCASE/signing-key",
    ],
)
def test_invalid_event_signing_ref_is_rejected_even_when_disabled(ref: str) -> None:
    with pytest.raises(PluginPlatformConfigError, match="PLUGIN_EVENT_SIGNING_SECRET_REF"):
        PluginPlatformConfig.from_settings(
            FakeSettings(plugin_platform_enabled=False, plugin_event_signing_secret_ref=ref)
        )


def test_k8s_event_signing_ref_is_typed_and_does_not_enable_platform() -> None:
    config = PluginPlatformConfig.from_settings(
        FakeSettings(
            plugin_platform_enabled=False,
            plugin_event_signing_secret_ref=(
                "k8s-secret://agentscale-plugin-events/signing-key"
            ),
        )
    )
    assert config.enabled is False
    assert config.event_signing_secret_ref.startswith("k8s-secret://")


def test_previous_secrets_are_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV,
        f" {TEST_OLD_SECRET_1} , , {TEST_OLD_SECRET_2} ",
    )
    config = PluginPlatformConfig.from_settings(FakeSettings())
    assert config.previous_claims_secrets == (TEST_OLD_SECRET_1, TEST_OLD_SECRET_2)


def test_environ_override_makes_rotation_deterministic() -> None:
    """Явный ``environ=`` позволяет тестам не трогать глобальный os.environ."""
    config = PluginPlatformConfig.from_settings(
        FakeSettings(),
        environ={PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV: TEST_OLD_SECRET_1},
    )
    assert config.previous_claims_secrets == (TEST_OLD_SECRET_1,)
    plain = PluginPlatformConfig.from_settings(FakeSettings(), environ={})
    assert plain.previous_claims_secrets == ()


def test_active_secret_in_previous_list_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PLUGIN_CLAIMS_PREVIOUS_SECRETS_ENV, TEST_SECRET)
    with pytest.raises(PluginPlatformConfigError, match="duplicate kid"):
        PluginPlatformConfig.from_settings(FakeSettings())


def test_read_previous_secrets_empty() -> None:
    assert read_previous_secrets(None) == ()
    assert read_previous_secrets("") == ()
    assert read_previous_secrets(" , , ") == ()


def test_report_never_contains_secrets(fake_settings: FakeSettings) -> None:
    report = PluginPlatformConfig.from_settings(fake_settings).as_report()
    serialized = repr(report)
    assert TEST_SECRET not in serialized
    assert fake_settings.plugin_event_signing_secret_ref not in serialized
    assert "claims_secret_configured" in report
    assert report["event_signing_secret_configured"] is True
    assert report["claims_secret_configured"] is True
    assert "previous_keys_count" in report


def test_with_overrides_revalidates_and_rejects_typos() -> None:
    config = PluginPlatformConfig(enabled=False)
    with pytest.raises(PluginPlatformConfigError, match="unknown"):
        config.with_overrides(claim_secret="x")  # опечатка: claims_secret
    # enabled=True включает полный набор guard'ов: помимо секрета и origin
    # обязателен keyless-cosign identity (см. test_enabled_without_cosign_identity_fails,
    # который требует именно этот отказ). Без него validate() упадёт по другой
    # причине и тест не проверит ревалидацию override'ов. Guard не ослабляем.
    updated = config.with_overrides(
        enabled=True,
        claims_secret=TEST_SECRET,
        event_signing_secret_ref=FakeSettings().plugin_event_signing_secret_ref,
        static_origin=TEST_STATIC_ORIGIN,
        shell_origin=TEST_SHELL_ORIGIN,
        cosign_identity=FakeSettings().plugin_cosign_identity,
    )
    assert updated.enabled is True
    assert config.enabled is False  # исходный frozen-объект не изменился
