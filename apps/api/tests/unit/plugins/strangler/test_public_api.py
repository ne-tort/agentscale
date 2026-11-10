"""Сквозная проверка публичного API зоны strangler и интеграционных контрактов.

Тесты проходят в текущем прогоне (pytest tests/unit/plugins).
"""

from __future__ import annotations

import inspect

import pytest

import prodavan.application.plugins.strangler as package
from prodavan.application.plugins.strangler import (
    FORBIDDEN_PARITY_NAMES,
    CutoverPlan,
    EnabledModulesPolicy,
    FeatureFlagStorePort,
    InMemoryFeatureFlagStore,
    PluginRouteError,
    StranglerRouter,
)

from .conftest import COMPANY, PLUGIN


def test_public_api_exports_are_importable() -> None:
    for name in package.__all__:
        assert hasattr(package, name), f"{name} объявлен в __all__, но отсутствует"


def test_forbidden_parity_names_absent_from_whole_public_api() -> None:
    """В публичном API НЕТ dual_read/dual_write/parity/snapshot/migrate_data."""
    public = set(package.__all__)
    for forbidden in FORBIDDEN_PARITY_NAMES:
        assert forbidden not in public
        assert not hasattr(package, forbidden)


def test_forbidden_names_absent_from_every_public_class() -> None:
    classes = (
        FeatureFlagStorePort,
        InMemoryFeatureFlagStore,
        EnabledModulesPolicy,
        StranglerRouter,
        PluginRouteError,
        CutoverPlan,
    )
    for cls in classes:
        names = set(vars(cls))
        for forbidden in FORBIDDEN_PARITY_NAMES:
            assert forbidden not in names, f"{cls.__name__}: {forbidden}"


def test_no_dual_or_migration_methods_in_source() -> None:
    """Ни одна функция/метод зоны не называется запрещённым именем."""
    from prodavan.application.plugins.strangler import contexts, cutover, flags, routing

    for module in (flags, routing, contexts, cutover):
        source = inspect.getsource(module)
        for forbidden in FORBIDDEN_PARITY_NAMES:
            assert f"def {forbidden}" not in source
            assert f"{forbidden}:" not in source  # нет одноимённых полей датаклассов


def test_admin_write_side_is_not_duplicated_here() -> None:
    """Сторона записи флага остаётся в admin/: здесь нет set_enabled_modules."""
    assert not hasattr(package, "set_enabled_modules")
    assert not hasattr(package, "EnabledModulesReceipt")
    assert not hasattr(package, "PluginAdminService")
    assert not hasattr(package, "GrantStorePort")


def test_strangler_router_uses_read_side_only() -> None:
    """Роутер читает флаг через EnabledModulesPolicy, а не пишет его."""
    router = StranglerRouter(EnabledModulesPolicy(InMemoryFeatureFlagStore()))
    assert isinstance(router.policy, EnabledModulesPolicy)
    assert "set" not in vars(StranglerRouter)


async def test_end_to_end_disabled_then_enabled_is_exactly_one_path() -> None:
    """Критерий §11 №16: флаг переключает путь без миграции данных."""
    store = InMemoryFeatureFlagStore()
    router = StranglerRouter(EnabledModulesPolicy(store))
    calls: list[str] = []

    def legacy() -> str:
        calls.append("legacy")
        return "old"

    def plugin() -> str:
        calls.append("plugin")
        return "new"

    assert await router.route(COMPANY, PLUGIN, legacy_handler=legacy, plugin_handler=plugin) == "old"
    store.set(COMPANY, [PLUGIN])
    assert await router.route(COMPANY, PLUGIN, legacy_handler=legacy, plugin_handler=plugin) == "new"
    assert calls == ["legacy", "plugin"]  # ни одного двойного вызова, без сравнения


@pytest.mark.parametrize("name", package.__all__)
def test_no_export_is_a_parity_concept(name: str) -> None:
    # Сам сторожевой список содержит запрещённые слова по определению — это не
    # «концепция парности в API», а перечень того, чего быть не должно.
    if name == "FORBIDDEN_PARITY_NAMES":
        return
    lowered = name.lower()
    for forbidden in FORBIDDEN_PARITY_NAMES:
        assert forbidden not in lowered
