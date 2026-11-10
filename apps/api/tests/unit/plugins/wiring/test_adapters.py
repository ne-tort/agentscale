"""Strict production AdapterRegistry composition (verification freeze)."""
from __future__ import annotations

from typing import Any

import pytest

from prodavan.application.plugins.capabilities.contracts import CapabilityName, DB_DIRECT
from prodavan.application.plugins.capabilities.ports import UnknownCapabilityError
from prodavan.application.plugins.capabilities.quotas import QuotaUsage
from prodavan.application.plugins.wiring.adapters import (
    UnavailableAdapter, aclose_adapter_registry, adapter_report, build_adapter_registry,
)
from prodavan.application.plugins.wiring.config import (
    PluginPlatformConfig, PluginPlatformConfigError,
)


class Full:
    def __init__(self, *, meter: bool = True, operations: bool = True) -> None:
        self._meter = meter
        self._operations = operations
        self.close_calls = 0

    def quota_usage(self, request: object) -> QuotaUsage:
        if not self._meter:
            raise AssertionError("test must remove this method with delattr")
        return QuotaUsage(ops=1, rows=0, bytes=0, keys=1, object_bytes=0, egress_bytes=0)

    def __getattr__(self, name: str) -> Any:
        if name == "quota_usage" and not self._meter:
            raise AttributeError(name)
        if name in {"get", "set", "delete", "incr", "publish"} and not self._operations:
            raise AttributeError(name)
        async def operation(request: object) -> object:
            return object()
        return operation

    async def aclose(self) -> None:
        self.close_calls += 1


def full() -> dict[str, object]:
    return {name.value: Full() for name in CapabilityName}


def test_registry_contains_nine_real_adapters(enabled_config: PluginPlatformConfig) -> None:
    registry = build_adapter_registry(enabled_config, **full())
    assert len(registry.registered) == 9
    assert DB_DIRECT not in registry.registered
    report = adapter_report(registry)
    assert report["stub_count"] == 0
    assert len(report["real_adapters"]) == 9
    assert report["quota_meter_required"] is True


def test_missing_adapters_fail_at_composition(enabled_config: PluginPlatformConfig) -> None:
    with pytest.raises(PluginPlatformConfigError, match="missing"):
        build_adapter_registry(enabled_config)
    with pytest.raises(PluginPlatformConfigError, match="cache"):
        build_adapter_registry(enabled_config, **{k: v for k, v in full().items() if k != "cache"})


def test_unavailable_is_never_a_fallback(enabled_config: PluginPlatformConfig) -> None:
    with pytest.raises(PluginPlatformConfigError, match="UnavailableAdapter"):
        build_adapter_registry(enabled_config, **{**full(), "cache": UnavailableAdapter("cache")})


def test_missing_quota_hook_fails(enabled_config: PluginPlatformConfig) -> None:
    overrides = full()
    overrides["docs"] = Full()
    overrides["docs"].quota_usage = None
    with pytest.raises(PluginPlatformConfigError, match="quota_usage"):
        build_adapter_registry(enabled_config, **overrides)


def test_unknown_and_db_direct_are_rejected(enabled_config: PluginPlatformConfig) -> None:
    with pytest.raises(UnknownCapabilityError):
        build_adapter_registry(enabled_config, **{**full(), DB_DIRECT: Full()})
    with pytest.raises(UnknownCapabilityError):
        build_adapter_registry(enabled_config, **{**full(), "cach": Full()})


def test_none_and_broken_adapters_are_rejected(enabled_config: PluginPlatformConfig) -> None:
    with pytest.raises(PluginPlatformConfigError, match="None"):
        build_adapter_registry(enabled_config, **{**full(), "cache": None})
    with pytest.raises(PluginPlatformConfigError, match="missing methods"):
        build_adapter_registry(enabled_config, **{**full(), "cache": Full(operations=False)})


def test_disabled_platform_has_no_registry(disabled_config: PluginPlatformConfig) -> None:
    with pytest.raises(PluginPlatformConfigError, match="disabled"):
        build_adapter_registry(disabled_config, **full())


@pytest.mark.asyncio
async def test_registry_close_is_once_per_distinct_adapter(enabled_config: PluginPlatformConfig) -> None:
    shared = Full()
    registry = build_adapter_registry(
        enabled_config, **{name.value: shared for name in CapabilityName}
    )
    await aclose_adapter_registry(registry)
    await aclose_adapter_registry(registry)
    assert shared.close_calls == 1
