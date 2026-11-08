"""F11: ``UnavailableAdapter`` и сборка ``AdapterRegistry`` (fail-closed).

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ (verification freeze).
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.capabilities.contracts import (
    CacheGetRequest,
    CapabilityEnvelope,
    DB_DIRECT,
)
from prodavan.application.plugins.capabilities.ports import (
    CAPABILITY_OPERATIONS,
    AdapterMissingError,
    UnknownCapabilityError,
)
from prodavan.application.plugins.wiring.adapters import (
    STUB_ADAPTER_NAMES,
    STUB_ADAPTER_WORK,
    WIRED_CAPABILITIES,
    UnavailableAdapter,
    adapter_report,
    build_adapter_registry,
    stub_names,
)
from prodavan.application.plugins.wiring.config import (
    PluginPlatformConfig,
    PluginPlatformConfigError,
)
from tests.unit.plugins.wiring.conftest import TEST_DIGEST


def test_registry_registers_exactly_nine_capabilities(
    enabled_config: PluginPlatformConfig,
) -> None:
    registry = build_adapter_registry(enabled_config)
    assert set(registry.registered) == set(WIRED_CAPABILITIES)
    assert len(registry.registered) == 9
    assert DB_DIRECT not in registry.registered


def test_db_direct_is_never_registered(enabled_config: PluginPlatformConfig) -> None:
    registry = build_adapter_registry(enabled_config)
    with pytest.raises(UnknownCapabilityError):
        registry.resolve(DB_DIRECT)
    with pytest.raises(UnknownCapabilityError):
        registry.register(DB_DIRECT, object())


def test_db_direct_override_is_rejected(enabled_config: PluginPlatformConfig) -> None:
    with pytest.raises(UnknownCapabilityError):
        build_adapter_registry(enabled_config, **{DB_DIRECT: object()})


def test_unknown_override_is_rejected(enabled_config: PluginPlatformConfig) -> None:
    with pytest.raises(UnknownCapabilityError):
        build_adapter_registry(enabled_config, cach=object())


def test_unavailable_adapter_raises_not_returns_none(
    enabled_config: PluginPlatformConfig,
) -> None:
    registry = build_adapter_registry(enabled_config)
    adapter = registry.resolve("cache")
    assert isinstance(adapter, UnavailableAdapter)

    envelope = CapabilityEnvelope(
        request_id="req_1",
        company_id="co_123",
        subject_id="plugin:mod_x:co_123:inst_abc",
        plugin_instance_id="inst_abc",
        artifact_digest=TEST_DIGEST,
        grant_id="grant_01",
    )
    request = CacheGetRequest(key="tenant:co_123:plugin:mod_x:inst_abc:k", envelope=envelope)
    with pytest.raises(AdapterMissingError) as ei:
        adapter.get(request)  # type: ignore[attr-defined]
    assert ei.value.name.startswith("cache.get")
    assert "not implemented" in str(ei.value)


def test_every_declared_operation_refuses(enabled_config: PluginPlatformConfig) -> None:
    registry = build_adapter_registry(enabled_config)
    for name, operations in CAPABILITY_OPERATIONS.items():
        adapter = registry.resolve(name)
        assert isinstance(adapter, UnavailableAdapter)
        assert tuple(sorted(operations)) == adapter.operations
        for operation in operations:
            method = getattr(adapter, operation)
            assert callable(method)
            with pytest.raises(AdapterMissingError):
                method(object())


def test_unavailable_adapter_has_no_foreign_methods() -> None:
    adapter = UnavailableAdapter("cache")
    with pytest.raises(AttributeError):
        _ = adapter.raw_sql  # type: ignore[attr-defined]


def test_unavailable_adapter_rejects_unknown_capability() -> None:
    with pytest.raises(UnknownCapabilityError):
        UnavailableAdapter(DB_DIRECT)


def test_real_override_is_registered_and_not_a_stub(
    enabled_config: PluginPlatformConfig,
) -> None:
    class RealCache:
        def get(self, request: object) -> object:
            return object()

        def set(self, request: object) -> object:
            return object()

        def delete(self, request: object) -> object:
            return object()

        def incr(self, request: object) -> object:
            return object()

        def publish(self, request: object) -> object:
            return object()

    registry = build_adapter_registry(enabled_config, cache=RealCache())
    assert isinstance(registry.resolve("cache"), RealCache)
    assert "cache" not in stub_names(registry, cache=RealCache())
    assert "cache" not in adapter_report(registry, cache=RealCache())["stub_adapters"]


def test_partial_override_without_methods_is_rejected(
    enabled_config: PluginPlatformConfig,
) -> None:
    class BrokenCache:
        def get(self, request: object) -> object:
            return object()

    with pytest.raises(PluginPlatformConfigError, match="missing methods"):
        build_adapter_registry(enabled_config, cache=BrokenCache())


def test_none_override_is_rejected(enabled_config: PluginPlatformConfig) -> None:
    with pytest.raises(PluginPlatformConfigError, match="is None"):
        build_adapter_registry(enabled_config, cache=None)


def test_disabled_platform_refuses_registry(disabled_config: PluginPlatformConfig) -> None:
    with pytest.raises(PluginPlatformConfigError, match="disabled"):
        build_adapter_registry(disabled_config)


def test_adapter_report_lists_all_stubs_explicitly(
    enabled_config: PluginPlatformConfig,
) -> None:
    registry = build_adapter_registry(enabled_config)
    report = adapter_report(registry)

    assert report["db_direct_registered"] is False
    assert report["stub_count"] == 9
    assert report["stub_adapters"] == list(STUB_ADAPTER_NAMES)
    assert report["real_adapters"] == []
    assert len(report["requires_f11_work"]) == 9
    for item in report["requires_f11_work"]:
        assert item["capability"] in WIRED_CAPABILITIES
        assert item["work"] == STUB_ADAPTER_WORK[item["capability"]]
        assert len(item["work"]) > 10  # не «пустая отписка»


def test_adapter_report_reflects_real_overrides(enabled_config: PluginPlatformConfig) -> None:
    class Full:
        def __getattr__(self, name: str) -> object:
            return lambda request: object()

    overrides = {name: Full() for name in WIRED_CAPABILITIES}
    registry = build_adapter_registry(enabled_config, **overrides)
    report = adapter_report(registry, **overrides)
    assert report["stub_count"] == 0
    assert report["real_adapters"] == list(WIRED_CAPABILITIES)
