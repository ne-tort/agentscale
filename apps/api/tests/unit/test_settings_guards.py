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

