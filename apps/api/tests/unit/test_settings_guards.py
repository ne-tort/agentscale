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
