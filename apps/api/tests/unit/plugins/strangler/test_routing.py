"""Тесты ``routing.py``: ровно ОДИН handler, fail-closed, typed error (§8, кр. 16).

Тесты НЕ запускались (verification freeze).
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.strangler import (
    EnabledModulesPolicy,
    InMemoryFeatureFlagStore,
    NotEnabled,
    PluginRouteError,
    StranglerRouter,
)

from .conftest import COMPANY, PLUGIN


class _Handlers:
    """Записывает, сколько раз вызван каждый обработчик, и хранит результаты."""

    def __init__(
        self,
        *,
        legacy_result: object = "legacy",
        plugin_result: object = "plugin",
        plugin_error: BaseException | None = None,
    ) -> None:
        self.legacy_calls = 0
        self.plugin_calls = 0
        self.legacy_result = legacy_result
        self.plugin_result = plugin_result
        self.plugin_error = plugin_error

    def legacy(self) -> object:
        self.legacy_calls += 1
        return self.legacy_result

    def plugin(self) -> object:
        self.plugin_calls += 1
        if self.plugin_error is not None:
            raise self.plugin_error
        return self.plugin_result

    def total(self) -> int:
        return self.legacy_calls + self.plugin_calls


def _router(store: InMemoryFeatureFlagStore) -> StranglerRouter:
    return StranglerRouter(EnabledModulesPolicy(store))


# ---------------------------------------------------------------- выбор пути


async def test_flag_disabled_routes_to_legacy_only(store: InMemoryFeatureFlagStore) -> None:
    """Флаг выключен → legacy; plugin НЕ вызывается."""
    handlers = _Handlers()
    result = await _router(store).route(
        COMPANY, PLUGIN, legacy_handler=handlers.legacy, plugin_handler=handlers.plugin
    )
    assert result == "legacy"
    assert handlers.legacy_calls == 1
    assert handlers.plugin_calls == 0
    assert handlers.total() == 1


async def test_flag_enabled_routes_to_plugin_only(store: InMemoryFeatureFlagStore) -> None:
    """Флаг включён → plugin; legacy НЕ вызывается."""
    store.set(COMPANY, [PLUGIN])
    handlers = _Handlers()
    result = await _router(store).route(
        COMPANY, PLUGIN, legacy_handler=handlers.legacy, plugin_handler=handlers.plugin
    )
    assert result == "plugin"
    assert handlers.plugin_calls == 1
    assert handlers.legacy_calls == 0
    assert handlers.total() == 1


async def test_exactly_one_handler_called_for_both_states(
    store: InMemoryFeatureFlagStore,
) -> None:
    """Ровно ОДИН handler вызван при каждом состоянии флага."""
    router = _router(store)
    for enabled in (False, True):
        store.set(COMPANY, [PLUGIN] if enabled else [])
        handlers = _Handlers()
        await router.route(
            COMPANY, PLUGIN, legacy_handler=handlers.legacy, plugin_handler=handlers.plugin
        )
        assert handlers.total() == 1


async def test_repeated_routes_never_call_both_handlers(
    store: InMemoryFeatureFlagStore,
) -> None:
    """Серия запросов не приводит ни к одному «двойному» вызову."""
    router = _router(store)
    handlers = _Handlers()
    for _ in range(5):
        await router.route(
            COMPANY, PLUGIN, legacy_handler=handlers.legacy, plugin_handler=handlers.plugin
        )
    store.set(COMPANY, [PLUGIN])
    for _ in range(5):
        await router.route(
            COMPANY, PLUGIN, legacy_handler=handlers.legacy, plugin_handler=handlers.plugin
        )
    assert handlers.legacy_calls == 5
    assert handlers.plugin_calls == 5
    assert handlers.total() == 10


async def test_async_handlers_are_awaited(store: InMemoryFeatureFlagStore) -> None:
    """Корутинные обработчики (реальный API-слой) тоже работают."""
    store.set(COMPANY, [PLUGIN])
    calls: list[str] = []

    async def legacy() -> str:
        calls.append("legacy")
        return "legacy"

    async def plugin() -> str:
        calls.append("plugin")
        return "plugin"

    result = await _router(store).route(
        COMPANY, PLUGIN, legacy_handler=legacy, plugin_handler=plugin
    )
    assert result == "plugin"
    assert calls == ["plugin"]  # ровно один, legacy не вызывался


# ---------------------------------------------------------------- fail-closed


async def test_unavailable_store_routes_to_legacy() -> None:
    """Хранилище недоступно → fail-closed → legacy (не «включить всё»)."""

    class BrokenStore:
        def enabled(self, company_id: str, plugin_id: str) -> bool:
            raise RuntimeError("flag DB down")

        def set(self, company_id: str, plugin_ids) -> None:
            raise RuntimeError("flag DB down")

        def list(self, company_id: str):
            raise RuntimeError("flag DB down")

    policy = EnabledModulesPolicy(BrokenStore())  # type: ignore[arg-type]
    handlers = _Handlers()
    result = await StranglerRouter(policy).route(
        COMPANY, PLUGIN, legacy_handler=handlers.legacy, plugin_handler=handlers.plugin
    )
    assert result == "legacy"
    assert handlers.legacy_calls == 1
    assert handlers.plugin_calls == 0


async def test_no_store_routes_to_legacy() -> None:
    """Политика без адаптера = отсутствие интеграции → legacy."""
    handlers = _Handlers()
    result = await StranglerRouter(EnabledModulesPolicy()).route(
        COMPANY, PLUGIN, legacy_handler=handlers.legacy, plugin_handler=handlers.plugin
    )
    assert result == "legacy"
    assert handlers.plugin_calls == 0


async def test_empty_identifiers_route_to_legacy(store: InMemoryFeatureFlagStore) -> None:
    store.set(COMPANY, [PLUGIN])
    router = _router(store)
    for company, plugin in (("", PLUGIN), (COMPANY, ""), ("  ", PLUGIN)):
        handlers = _Handlers()
        result = await router.route(
            company, plugin, legacy_handler=handlers.legacy, plugin_handler=handlers.plugin
        )
        assert result == "legacy"
        assert handlers.plugin_calls == 0


def test_not_enabled_signal_is_a_named_condition() -> None:
    """``NotEnabled`` фиксирует причину выбора legacy (не безликий if)."""
    signal = NotEnabled(company_id=COMPANY, plugin_id=PLUGIN, reason="disabled")
    assert signal.company_id == COMPANY
    assert signal.plugin_id == PLUGIN
    assert "disabled" in signal.reason


def test_not_enabled_is_caught_internally_not_exposed() -> None:
    """``NotEnabled`` не должен пробрасываться наружу из публичного API."""
    from prodavan.application.plugins.strangler import routing

    assert "route" in vars(routing.StranglerRouter)
    assert "_decide" in vars(routing.StranglerRouter)


# ---------------------------------------------------------------- ошибка plugin-пути


async def test_plugin_error_is_not_silently_fallen_back_to_legacy(
    store: InMemoryFeatureFlagStore,
) -> None:
    """Главное требование: ошибка plugin-пути НЕ маскируется legacy-путем."""
    store.set(COMPANY, [PLUGIN])
    handlers = _Handlers(plugin_error=RuntimeError("plugin backend 500"))
    with pytest.raises(PluginRouteError) as ei:
        await _router(store).route(
            COMPANY, PLUGIN, legacy_handler=handlers.legacy, plugin_handler=handlers.plugin
        )
    # Legacy НЕ вызван: баг плагина виден, а не скрыт.
    assert handlers.legacy_calls == 0
    assert handlers.plugin_calls == 1
    assert isinstance(ei.value.original, RuntimeError)
    assert "plugin backend 500" in str(ei.value.original)
    assert ei.value.__cause__ is ei.value.original


async def test_plugin_route_error_is_typed_and_carries_context(
    store: InMemoryFeatureFlagStore,
) -> None:
    store.set(COMPANY, [PLUGIN])
    handlers = _Handlers(plugin_error=ValueError("bad typed contract"))
    with pytest.raises(PluginRouteError) as ei:
        await _router(store).route(
            COMPANY, PLUGIN, legacy_handler=handlers.legacy, plugin_handler=handlers.plugin
        )
    error = ei.value
    assert error.code == "PLUGIN_ROUTE_FAILED"
    assert error.status == 502
    assert error.extra["company_id"] == COMPANY
    assert error.extra["plugin_id"] == PLUGIN


async def test_nested_plugin_route_error_is_not_double_wrapped(
    store: InMemoryFeatureFlagStore,
) -> None:
    store.set(COMPANY, [PLUGIN])
    inner = PluginRouteError(
        company_id=COMPANY, plugin_id=PLUGIN, original=RuntimeError("nested")
    )

    def plugin() -> object:
        raise inner

    with pytest.raises(PluginRouteError) as ei:
        await _router(store).route(
            COMPANY, PLUGIN, legacy_handler=lambda: "legacy", plugin_handler=plugin
        )
    assert ei.value is inner  # обёртка не дублируется


async def test_legacy_error_propagates_as_is(store: InMemoryFeatureFlagStore) -> None:
    """Ошибка legacy-пути не подменяется plugin-путём и не оборачивается."""

    def legacy() -> object:
        raise ValueError("legacy bug")

    with pytest.raises(ValueError, match="legacy bug"):
        await _router(store).route(
            COMPANY, PLUGIN, legacy_handler=legacy, plugin_handler=lambda: "plugin"
        )


async def test_no_comparison_of_results(store: InMemoryFeatureFlagStore) -> None:
    """Результаты путей не сравниваются: второй handler физически не вызывается."""
    store.set(COMPANY, [PLUGIN])
    handlers = _Handlers(legacy_result={"a": 1}, plugin_result={"b": 2})
    result = await _router(store).route(
        COMPANY, PLUGIN, legacy_handler=handlers.legacy, plugin_handler=handlers.plugin
    )
    assert result == {"b": 2}
    assert handlers.legacy_calls == 0


def test_router_requires_policy() -> None:
    with pytest.raises(ValueError):
        StranglerRouter(None)  # type: ignore[arg-type]


async def test_non_callable_handlers_are_rejected(store: InMemoryFeatureFlagStore) -> None:
    with pytest.raises(TypeError):
        await _router(store).route(
            COMPANY,
            PLUGIN,
            legacy_handler="not callable",  # type: ignore[arg-type]
            plugin_handler=lambda: "plugin",
        )


async def test_forbidden_parity_names_absent_from_router() -> None:
    """У роутера нет dual_read/parity/snapshot/migrate_data (§13.1)."""
    from prodavan.application.plugins.strangler import FORBIDDEN_PARITY_NAMES, routing

    public = set(vars(routing.StranglerRouter)) | set(routing.__all__)
    for forbidden in FORBIDDEN_PARITY_NAMES:
        assert forbidden not in public
