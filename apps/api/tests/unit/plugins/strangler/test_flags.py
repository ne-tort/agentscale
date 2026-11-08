"""Тесты ``flags.py``: fail-closed политика и запрет parity-API (§8, §13.1, кр. 16).

Тесты НЕ запускались (verification freeze).
"""

from __future__ import annotations

import inspect

import pytest

from prodavan.application.plugins.strangler import (
    FORBIDDEN_PARITY_NAMES,
    EnabledModulesPolicy,
    FeatureFlagStorePort,
    InMemoryFeatureFlagStore,
)
from prodavan.application.plugins.strangler import flags as flags_module

from .conftest import COMPANY, OTHER_COMPANY, OTHER_PLUGIN, PLUGIN


# ---------------------------------------------------------------- InMemory store


def test_in_memory_store_implements_port_structurally() -> None:
    store: FeatureFlagStorePort = InMemoryFeatureFlagStore()
    assert isinstance(store, FeatureFlagStorePort)


def test_port_exposes_exactly_three_operations() -> None:
    """Порт умеет ровно enabled/set/list и ничего более (§8: не dual-run)."""
    port_members = {
        name
        for name in dir(FeatureFlagStorePort)
        if not name.startswith("_") and callable(getattr(FeatureFlagStorePort, name, None))
    }
    assert {"enabled", "set", "list"} <= port_members
    for forbidden in FORBIDDEN_PARITY_NAMES:
        assert forbidden not in port_members


def test_store_set_list_are_per_company() -> None:
    store = InMemoryFeatureFlagStore()
    store.set(COMPANY, [PLUGIN, OTHER_PLUGIN])
    assert store.enabled(COMPANY, PLUGIN) is True
    assert store.enabled(COMPANY, OTHER_PLUGIN) is True
    # "mod_equipment" < "mod_management" → отсортированный кортеж.
    assert store.list(COMPANY) == (PLUGIN, OTHER_PLUGIN)
    # Другая компания не видит чужие включения.
    assert store.list(OTHER_COMPANY) == ()
    assert store.enabled(OTHER_COMPANY, PLUGIN) is False


def test_store_rejects_empty_company_on_set() -> None:
    store = InMemoryFeatureFlagStore()
    with pytest.raises(ValueError):
        store.set("  ", [PLUGIN])


def test_store_empty_identifiers_are_never_enabled() -> None:
    store = InMemoryFeatureFlagStore(initial={COMPANY: [PLUGIN]})
    assert store.enabled("", PLUGIN) is False
    assert store.enabled(COMPANY, "") is False
    assert store.list("") == ()


# ---------------------------------------------------------------- EnabledModulesPolicy


def test_policy_disabled_by_default_is_fail_closed(store: InMemoryFeatureFlagStore) -> None:
    policy = EnabledModulesPolicy(store)
    assert policy.is_enabled(COMPANY, PLUGIN) is False
    assert policy.enabled_modules(COMPANY) == ()


def test_policy_enabled_after_set(store: InMemoryFeatureFlagStore) -> None:
    policy = EnabledModulesPolicy(store)
    store.set(COMPANY, [PLUGIN])
    assert policy.is_enabled(COMPANY, PLUGIN) is True
    assert PLUGIN in policy.enabled_modules(COMPANY)


def test_policy_without_store_is_fail_closed() -> None:
    """Отсутствие интеграции = модуль НЕ включён (остаёмся на legacy)."""
    policy = EnabledModulesPolicy()
    assert policy.store is None
    assert policy.is_enabled(COMPANY, PLUGIN) is False
    assert policy.enabled_modules(COMPANY) == ()


def test_policy_bind_attaches_store() -> None:
    policy = EnabledModulesPolicy()
    assert policy.is_enabled(COMPANY, PLUGIN) is False
    policy.bind(InMemoryFeatureFlagStore(initial={COMPANY: [PLUGIN]}))
    assert policy.is_enabled(COMPANY, PLUGIN) is True


def test_unavailable_store_is_fail_closed_legacy() -> None:
    """Хранилище бросает исключение → считаем модуль НЕ включённым, а не «включить всё»."""

    class BrokenStore:
        def enabled(self, company_id: str, plugin_id: str) -> bool:
            raise RuntimeError("flag DB down")

        def set(self, company_id: str, plugin_ids) -> None:
            raise RuntimeError("flag DB down")

        def list(self, company_id: str):
            raise RuntimeError("flag DB down")

    policy = EnabledModulesPolicy(BrokenStore())  # type: ignore[arg-type]
    assert policy.is_enabled(COMPANY, PLUGIN) is False
    assert policy.enabled_modules(COMPANY) == ()


def test_store_raising_only_on_list_is_fail_closed() -> None:
    class HalfBrokenStore:
        def enabled(self, company_id: str, plugin_id: str) -> bool:
            return True

        def set(self, company_id: str, plugin_ids) -> None:
            return None

        def list(self, company_id: str):
            raise RuntimeError("list unavailable")

    policy = EnabledModulesPolicy(HalfBrokenStore())  # type: ignore[arg-type]
    assert policy.is_enabled(COMPANY, PLUGIN) is True
    assert policy.enabled_modules(COMPANY) == ()


def test_non_bool_truthy_store_answer_is_not_an_enable() -> None:
    """Строгая проверка типа: 1 / 'yes' / объект ≠ разрешение."""

    class WeirdStore:
        def enabled(self, company_id: str, plugin_id: str):
            return 1

        def set(self, company_id: str, plugin_ids) -> None:
            return None

        def list(self, company_id: str):
            return [PLUGIN]

    policy = EnabledModulesPolicy(WeirdStore())  # type: ignore[arg-type]
    assert policy.is_enabled(COMPANY, PLUGIN) is False


def test_malformed_list_answer_is_fail_closed() -> None:
    class WeirdStore:
        def enabled(self, company_id: str, plugin_id: str) -> bool:
            return True

        def set(self, company_id: str, plugin_ids) -> None:
            return None

        def list(self, company_id: str):
            return "everything-enabled"  # не коллекция

    policy = EnabledModulesPolicy(WeirdStore())  # type: ignore[arg-type]
    assert policy.enabled_modules(COMPANY) == ()


def test_empty_identifiers_are_fail_closed_even_with_enabled_store() -> None:
    store = InMemoryFeatureFlagStore(initial={COMPANY: [PLUGIN]})
    policy = EnabledModulesPolicy(store)
    assert policy.is_enabled("", PLUGIN) is False
    assert policy.is_enabled(COMPANY, "") is False
    assert policy.is_enabled("   ", PLUGIN) is False


# ---------------------------------------------------------------- запрет parity-API


def test_forbidden_names_absent_from_public_api() -> None:
    """В публичном API НЕТ dual_read/snapshot/migrate_data и им подобных (§13.1)."""
    import prodavan.application.plugins.strangler as package

    public = set(package.__all__)
    for forbidden in FORBIDDEN_PARITY_NAMES:
        assert forbidden not in public
        assert not hasattr(package, forbidden)
        assert not hasattr(flags_module, forbidden)
        assert not hasattr(EnabledModulesPolicy, forbidden)
        assert not hasattr(InMemoryFeatureFlagStore, forbidden)
        assert not hasattr(FeatureFlagStorePort, forbidden)


def test_forbidden_names_absent_from_all_strangler_modules() -> None:
    """Проверяем все 4 модуля зоны, а не только __init__."""
    from prodavan.application.plugins.strangler import contexts, cutover, routing

    for module in (flags_module, routing, contexts, cutover):
        names = set(vars(module)) | set(getattr(module, "__all__", ()))
        for forbidden in FORBIDDEN_PARITY_NAMES:
            assert forbidden not in names, f"{module.__name__} содержит {forbidden}"


def test_policy_public_methods_contain_no_parity_operations() -> None:
    """Методы политики — только чтение флага; переноса/сверки нет."""
    methods = {
        name
        for name, _ in inspect.getmembers(EnabledModulesPolicy, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    assert "is_enabled" in methods
    assert "enabled_modules" in methods
    assert "bind" in methods
    for forbidden in FORBIDDEN_PARITY_NAMES:
        assert forbidden not in methods


def test_no_dual_run_source_markers_in_flags_module() -> None:
    """Исходный текст модуля не содержит запрещённых идентификаторов как кода.

    Упоминания в docstring допустимы (они фиксируют запрет), поэтому проверяем
    именно отсутствие их в сигнатурах/атрибутах, а не «ни одного упоминания».
    """
    source = inspect.getsource(flags_module)
    for forbidden in ("def dual_read", "def dual_write", "def snapshot", "def migrate_data", "def parity"):
        assert forbidden not in source
