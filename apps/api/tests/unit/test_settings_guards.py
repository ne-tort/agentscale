"""Unit tests — settings guards for dev-only flags (audit API-P2c)."""

from __future__ import annotations

import pytest


def test_default_environment_is_dev() -> None:
    from prodavan.config.settings import Settings

    s = Settings()
    assert s.app_environment == "dev"
    assert s.agent_inprocess_adapters_enabled is False


def test_inprocess_adapters_allowed_in_dev() -> None:
    from prodavan.config.settings import Settings

    s = Settings(agent_inprocess_adapters_enabled=True)
    assert s.agent_inprocess_adapters_enabled is True


def test_inprocess_adapters_forbidden_in_prod() -> None:
    from prodavan.config.settings import Settings

    with pytest.raises(RuntimeError, match="forbidden outside dev"):
        Settings(app_environment="prod", agent_inprocess_adapters_enabled=True)


def test_inprocess_adapters_forbidden_in_staging() -> None:
    from prodavan.config.settings import Settings

    with pytest.raises(RuntimeError, match="forbidden outside dev"):
        Settings(app_environment="staging", agent_inprocess_adapters_enabled=True)


def test_inprocess_adapters_off_in_prod_is_ok() -> None:
    from prodavan.config.settings import Settings

    s = Settings(app_environment="prod")
    assert s.agent_inprocess_adapters_enabled is False


# --- pod probe address -------------------------------------------------------
#
# The probe pod's Service name carries the environment's namespace. A code
# default outlived the `prodavan` namespace and pointed at a Service that no
# longer existed, so a probe with the flag on failed with a DNS error at runtime
# instead of at startup. Enabling the switch now requires the address.


def test_pod_probe_is_off_and_addressed_by_nobody_by_default() -> None:
    from prodavan.config.settings import Settings

    s = Settings()
    assert s.pod_probe_enabled is False
    assert s.pod_probe_base_url == ""


def test_pod_probe_address_has_no_baked_in_default() -> None:
    from prodavan.config import settings as settings_module

    field = settings_module.Settings.model_fields["pod_probe_base_url"]
    assert field.default == "", (
        "a default address rots with the namespace it names; it belongs to the deployment"
    )


def test_pod_probe_enabled_without_address_fails_at_startup() -> None:
    from prodavan.config.settings import Settings

    with pytest.raises(RuntimeError, match="POD_PROBE_BASE_URL"):
        Settings(pod_probe_enabled=True, pod_probe_base_url="   ")


def test_pod_probe_enabled_with_address_is_accepted() -> None:
    from prodavan.config.settings import Settings

    s = Settings(
        pod_probe_enabled=True,
        pod_probe_base_url="http://agentscale-probe-pod.agentscale-dev-sandboxes.svc:3921",
    )
    assert s.pod_probe_enabled is True


def test_pod_probe_disabled_needs_no_address() -> None:
    from prodavan.config.settings import Settings

    s = Settings(app_environment="prod", pod_probe_enabled=False)
    assert s.pod_probe_base_url == ""


# --- plugin platform ---------------------------------------------------------
#
# Plugin-платформа — отдельный trust-контур (НЕ pod-gateway :8001). Включать её
# без секрета подписи или без отдельного static-origin нельзя: иначе любой
# PluginClaims принимается, а iframe оказывается на том же origin, что shell.


def test_plugin_platform_is_off_by_default() -> None:
    from prodavan.config.settings import Settings

    s = Settings()
    assert s.plugin_platform_enabled is False
    assert s.plugin_claims_secret == ""
    assert s.plugin_event_signing_secret_ref == ""
    assert s.plugin_static_origin == ""
    assert s.plugin_shell_origin == ""
    assert s.plugin_egress_mode == "none"


def test_plugin_platform_enabled_without_secret_fails() -> None:
    from prodavan.config.settings import Settings

    with pytest.raises(RuntimeError, match="PLUGIN_CLAIMS_SECRET"):
        Settings(plugin_platform_enabled=True, plugin_static_origin="https://plugins.example.dev")


def test_plugin_platform_enabled_without_static_origin_fails() -> None:
    from prodavan.config.settings import Settings

    with pytest.raises(RuntimeError, match="PLUGIN_STATIC_ORIGIN"):
        Settings(plugin_platform_enabled=True, plugin_claims_secret="s3cret")


def test_plugin_platform_enabled_without_shell_origin_fails() -> None:
    from prodavan.config.settings import Settings

    with pytest.raises(RuntimeError, match="PLUGIN_SHELL_ORIGIN"):
        Settings(
            plugin_platform_enabled=True,
            plugin_claims_secret="s3cret",
            plugin_static_origin="https://plugins.example.dev",
        )


@pytest.mark.parametrize(
    "shell_origin",
    [
        "http://agentscale.dev",
        "https://*.agentscale.dev",
        "https://agentscale.dev/path",
        "https://agentscale.dev/?token=x",
        "https://plugins.example.dev",
    ],
)
def test_plugin_platform_shell_origin_is_exact_https_and_distinct(
    shell_origin: str,
) -> None:
    from prodavan.config.settings import Settings

    with pytest.raises(RuntimeError, match="PLUGIN_SHELL_ORIGIN|distinct"):
        Settings(
            plugin_platform_enabled=True,
            plugin_claims_secret="s3cret",
            plugin_static_origin="https://plugins.example.dev",
            plugin_shell_origin=shell_origin,
        )


def test_plugin_platform_valid_configuration_is_accepted() -> None:
    from prodavan.config.settings import Settings

    s = Settings(
        plugin_platform_enabled=True,
        plugin_claims_secret="s3cret",
        plugin_event_signing_secret_ref=(
            "k8s-secret://agentscale-plugin-events/signing-key"
        ),
        plugin_static_origin="https://plugin-origin.agentscale.dev",
        plugin_shell_origin="https://agentscale.dev",
    )
    assert s.plugin_claims_ttl_seconds == 300
    assert s.plugin_claims_issuer == "agentscale-plugins"
    assert s.plugin_claims_audience == "capability-gateway"


def test_plugin_enabled_requires_separate_event_signing_ref() -> None:
    from prodavan.config.settings import Settings

    with pytest.raises(RuntimeError, match="PLUGIN_EVENT_SIGNING_SECRET_REF"):
        Settings(
            plugin_platform_enabled=True,
            plugin_claims_secret="s3cret",
            plugin_static_origin="https://plugin-origin.agentscale.dev",
            plugin_shell_origin="https://agentscale.dev",
        )


@pytest.mark.parametrize("ref", ["plaintext", "k8s-secret://missing-key"])
def test_plugin_event_signing_ref_shape_is_validated(ref: str) -> None:
    from prodavan.config.settings import Settings

    with pytest.raises(RuntimeError, match="PLUGIN_EVENT_SIGNING_SECRET_REF"):
        Settings(plugin_event_signing_secret_ref=ref)


def test_plugin_full_egress_requires_explicit_approval() -> None:
    from prodavan.config.settings import Settings

    with pytest.raises(RuntimeError, match="PLUGIN_EGRESS_APPROVED"):
        Settings(plugin_egress_mode="full")


def test_plugin_unknown_egress_mode_rejected() -> None:
    from prodavan.config.settings import Settings

    with pytest.raises(RuntimeError, match="PLUGIN_EGRESS_MODE must be"):
        Settings(plugin_egress_mode="everything")


def test_plugin_claims_ttl_is_bounded() -> None:
    from prodavan.config.settings import Settings

    with pytest.raises(RuntimeError, match="60..900"):
        Settings(plugin_claims_ttl_seconds=86400)


def test_plugin_disabled_ignores_missing_secret_and_origin() -> None:
    from prodavan.config.settings import Settings

    s = Settings(app_environment="prod", plugin_platform_enabled=False)
    assert s.plugin_platform_enabled is False



def test_dev_manifests_supply_every_secret_the_kafka_guard_requires() -> None:
    """Гейт `KAFKA_CONSUMER_ENABLED` требовал секреты, которых не было в манифестах.

    Settings отказывается стартовать без `AUTH_COMMAND_SIGNING_SECRET` и
    `AUTH_EVENT_SIGNING_SECRET` (>=32 байта), а dev-конфиг включает kafka consumer
    и не задавал ни одного из них: API и celery падали в CrashLoopBackOff, и
    понять это из `verify-dev` было нельзя. Тест сверяет манифест с гейтом,
    чтобы следующее новое обязательное значение не оказалось только в коде.
    """

    from pathlib import Path

    import yaml

    repo_root = Path(__file__).resolve().parents[4]
    secret_path = repo_root / "infra" / "k3s" / "base" / "agentscale-api" / "secret.yaml"
    configmap_path = repo_root / "infra" / "k3s" / "base" / "agentscale-api" / "configmap.yaml"
    secret = yaml.safe_load(secret_path.read_text(encoding="utf-8"))
    configmap = yaml.safe_load(configmap_path.read_text(encoding="utf-8"))

    kafka_enabled = str(configmap["data"].get("KAFKA_CONSUMER_ENABLED", "")).strip().lower()
    if kafka_enabled != "true":
        pytest.skip("kafka consumer is disabled in the base configmap")

    values = secret["stringData"]
    for name in ("AUTH_COMMAND_SIGNING_SECRET", "AUTH_EVENT_SIGNING_SECRET"):
        assert name in values, f"{name} is required by KAFKA_CONSUMER_ENABLED"
        assert len(str(values[name]).encode("utf-8")) >= 32, name
