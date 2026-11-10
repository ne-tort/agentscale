"""Runtime backend contract: написано, намеренно НЕ запускается (freeze)."""

from __future__ import annotations

import pytest

from prodavan.infrastructure.plugins.runtime.config import (
    ENV_IMAGE_PULL_SECRET,
    InternalGatewayUrl,
    RuntimeConfig,
)
from prodavan.infrastructure.plugins.runtime.errors import RuntimeConfigError


def test_prefix_config_and_unit_default_are_canonical() -> None:
    from_env = RuntimeConfig.from_env(
        env={
            "PLUGIN_RUNTIME_NAMESPACE": "agentscale-dev",
            "PLUGIN_RUNTIME_DEPLOYMENT_PREFIX": "plugin",
        }
    )
    direct = RuntimeConfig(namespace="agentscale-dev", deployment_prefix="plugin")
    default = RuntimeConfig(namespace="agentscale-dev")
    assert from_env.deployment_prefix == "plugin-"
    assert direct.deployment_prefix == default.deployment_prefix == "plugin-"


@pytest.mark.parametrize("prefix", ["1plugin", "-plugin", "Plugin", "plugin--"])
def test_invalid_dns1035_prefix_is_rejected(prefix: str) -> None:
    with pytest.raises(RuntimeConfigError):
        RuntimeConfig(namespace="agentscale-dev", deployment_prefix=prefix)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("service_port", 8081),
        ("health_path", "/ready"),
        ("service_suffix", "backend"),
        ("state_configmap", "other-state"),
    ],
)
def test_unsupported_backend_overrides_are_rejected(field: str, value) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(RuntimeConfigError, match="descriptor|canonical|activation"):
        RuntimeConfig(namespace="agentscale-dev", **{field: value})


@pytest.mark.parametrize(
    ("env_name", "value"),
    [
        ("PLUGIN_RUNTIME_SERVICE_PORT", "8081"),
        ("PLUGIN_RUNTIME_HEALTH_PATH", "/ready"),
        ("PLUGIN_RUNTIME_SERVICE_SUFFIX", "backend"),
        ("PLUGIN_RUNTIME_STATE_CONFIGMAP", "other-state"),
    ],
)
def test_unsupported_env_overrides_are_not_silently_accepted(
    env_name: str, value: str
) -> None:
    with pytest.raises(RuntimeConfigError, match="descriptor|canonical|activation"):
        RuntimeConfig.from_env(
            env={"PLUGIN_RUNTIME_NAMESPACE": "agentscale-dev", env_name: value}
        )


def test_image_pull_secret_is_optional_and_managed_config_accepts_only_name() -> None:
    namespace = {"PLUGIN_RUNTIME_NAMESPACE": "agentscale-dev"}
    assert RuntimeConfig(namespace="agentscale-dev").image_pull_secret is None
    assert RuntimeConfig.from_env(env=namespace).image_pull_secret is None
    assert RuntimeConfig(
        namespace="agentscale-dev", image_pull_secret=""
    ).image_pull_secret is None
    assert RuntimeConfig.from_env(
        env={**namespace, ENV_IMAGE_PULL_SECRET: " "}
    ).image_pull_secret is None

    direct = RuntimeConfig(
        namespace="agentscale-dev", image_pull_secret="plugin-ghcr-pull"
    )
    managed = RuntimeConfig.from_env(
        env={**namespace, ENV_IMAGE_PULL_SECRET: "plugin-ghcr-pull"}
    )
    assert direct.image_pull_secret == "plugin-ghcr-pull"
    assert managed.image_pull_secret == direct.image_pull_secret


@pytest.mark.parametrize(
    "value",
    [
        "ghcr.io/ne-tort/plugin-ghcr-pull",
        "https://ghcr.io/plugin-ghcr-pull",
        "plugin-ghcr-pull,other",
        "PLUGIN-GHCR-PULL",
        ".plugin-ghcr-pull",
        "plugin..ghcr-pull",
        "plugin_ghcr_pull",
        "registry_read_token",
        '{"auths":{}}',
        "a" * 254,
    ],
)
def test_image_pull_secret_rejects_credentials_urls_and_invalid_names(value: str) -> None:
    with pytest.raises(RuntimeConfigError):
        RuntimeConfig(namespace="agentscale-dev", image_pull_secret=value)
    with pytest.raises(RuntimeConfigError):
        RuntimeConfig.from_env(
            env={
                "PLUGIN_RUNTIME_NAMESPACE": "agentscale-dev",
                ENV_IMAGE_PULL_SECRET: value,
            }
        )


def test_internal_gateway_url_is_typed_and_optional() -> None:
    missing = RuntimeConfig(namespace="agentscale-dev")
    assert missing.internal_gateway_url() is None
    configured = RuntimeConfig.from_env(
        env={
            "PLUGIN_RUNTIME_NAMESPACE": "agentscale-dev",
            "PLUGIN_RUNTIME_GATEWAY_URL": (
                "http://plugin-gateway.agentscale-dev.svc:8080"
            ),
        }
    )
    gateway = configured.internal_gateway_url()
    assert isinstance(gateway, InternalGatewayUrl)
    assert gateway == "http://plugin-gateway.agentscale-dev.svc:8080"


@pytest.mark.parametrize(
    "value",
    [
        "https://plugins.example.com",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://user:password@plugin-gateway:8080",
        "http://plugin-gateway:8080/capabilities",
        "http://plugin-gateway:8080?tenant=other",
        "ftp://plugin-gateway",
        "not-a-url",
    ],
)
def test_external_or_malformed_gateway_url_is_rejected_without_fallback(value: str) -> None:
    config = RuntimeConfig(namespace="agentscale-dev", gateway_url=value)
    with pytest.raises(RuntimeConfigError) as caught:
        config.internal_gateway_url()
    assert "password" not in str(caught.value)
    assert "other" not in str(caught.value)
