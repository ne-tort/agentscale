"""Общие фикстуры зоны strangler."""

from __future__ import annotations

import pytest

from prodavan.application.plugins.strangler import EnabledModulesPolicy, InMemoryFeatureFlagStore

COMPANY = "co_123"
OTHER_COMPANY = "co_456"
PLUGIN = "mod_equipment"
OTHER_PLUGIN = "mod_management"


@pytest.fixture
def store() -> InMemoryFeatureFlagStore:
    """Пустое in-memory хранилище флага."""
    return InMemoryFeatureFlagStore()


@pytest.fixture
def policy(store: InMemoryFeatureFlagStore) -> EnabledModulesPolicy:
    """Политика с подключённым хранилищем."""
    return EnabledModulesPolicy(store)
