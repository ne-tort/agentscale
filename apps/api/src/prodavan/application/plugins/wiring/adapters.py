"""Strict production AdapterRegistry composition.

An enabled registry contains nine real typed adapters. Missing implementations,
UnavailableAdapter, missing operations and missing QuotaMeterPort hooks are
startup errors; no runtime placeholder is installed on a mandatory path.
"""
from __future__ import annotations

from typing import Any, Final, Mapping

from prodavan.application.plugins.capabilities.contracts import (
    DB_DIRECT, PLUGIN_CAPABILITIES, CapabilityName, TypedResponse,
)
from prodavan.application.plugins.capabilities.ports import (
    CAPABILITY_OPERATIONS, AdapterMissingError, AdapterRegistry, UnknownCapabilityError,
)
from prodavan.application.plugins.wiring.config import (
    PluginPlatformConfig, PluginPlatformConfigError,
)

WIRED_CAPABILITIES: Final[tuple[str, ...]] = tuple(name.value for name in CapabilityName)
# Compatibility names for external report consumers; these are capabilities that
# used to require work, not adapters that may now be absent.
STUB_ADAPTER_NAMES: Final[tuple[str, ...]] = WIRED_CAPABILITIES
STUB_ADAPTER_WORK: Final[Mapping[str, str]] = {
    name: "real typed adapter and QuotaMeterPort are mandatory at composition"
    for name in WIRED_CAPABILITIES
}


class UnavailableAdapter:
    """Legacy direct-call sentinel, never accepted by build_adapter_registry."""

    def __init__(self, name: str) -> None:
        if name not in CAPABILITY_OPERATIONS:
            raise UnknownCapabilityError(name)
        self._name = name
        self._operations = tuple(sorted(CAPABILITY_OPERATIONS[name]))

    @property
    def operations(self) -> tuple[str, ...]:
        return self._operations

    def __getattr__(self, operation: str) -> Any:
        if operation not in self._operations:
            raise AttributeError(operation)
        async def refuse(request: object) -> TypedResponse:
            raise AdapterMissingError(f"{self._name}.{operation}: real adapter required")
        refuse.__name__ = operation
        return refuse


def _adapter_from_override(name: str, impl: object) -> object:
    if impl is None:
        raise PluginPlatformConfigError(f"adapter override for {name!r} is None")
    if isinstance(impl, UnavailableAdapter):
        raise PluginPlatformConfigError(
            f"adapter override for {name!r} is UnavailableAdapter; enabled registry forbids it"
        )
    missing = [
        method for method in CAPABILITY_OPERATIONS[name]
        if not callable(getattr(impl, method, None))
    ]
    if not callable(getattr(impl, "quota_usage", None)):
        missing.append("quota_usage")
    if missing:
        raise PluginPlatformConfigError(
            f"adapter override for {name!r} does not implement its protocol: "
            f"missing methods {sorted(missing)}"
        )
    return impl


def build_adapter_registry(
    config: PluginPlatformConfig,
    **overrides: object,
) -> AdapterRegistry:
    if not isinstance(config, PluginPlatformConfig):
        raise PluginPlatformConfigError("build_adapter_registry expects PluginPlatformConfig")
    config.validate()
    if not config.enabled:
        raise PluginPlatformConfigError("refusing to build an AdapterRegistry for a disabled platform")
    unknown = sorted(set(overrides) - set(WIRED_CAPABILITIES))
    if DB_DIRECT in overrides:
        raise UnknownCapabilityError(DB_DIRECT)
    if unknown:
        raise UnknownCapabilityError(unknown[0])
    if set(WIRED_CAPABILITIES) != set(PLUGIN_CAPABILITIES):
        raise PluginPlatformConfigError("capability registry definitions diverge")
    missing = [name for name in WIRED_CAPABILITIES if name not in overrides]
    if missing:
        raise PluginPlatformConfigError(
            "enabled plugin platform requires real typed adapters for every capability; "
            f"missing: {', '.join(missing)}"
        )
    registry = AdapterRegistry()
    for name in WIRED_CAPABILITIES:
        registry.register(name, _adapter_from_override(name, overrides[name]))
    return registry


def stub_names(registry: AdapterRegistry, **overrides: object) -> tuple[str, ...]:
    del overrides
    result: list[str] = []
    for name in WIRED_CAPABILITIES:
        try:
            if isinstance(registry.resolve(name), UnavailableAdapter):
                result.append(name)
        except (AdapterMissingError, UnknownCapabilityError):
            result.append(name)
    return tuple(result)


def adapter_report(registry: AdapterRegistry, **overrides: object) -> Mapping[str, Any]:
    stubs = list(stub_names(registry, **overrides))
    real = [name for name in WIRED_CAPABILITIES if name not in stubs]
    return {
        "registered": sorted(registry.registered),
        "db_direct_registered": DB_DIRECT in registry.registered,
        "real_adapters": real,
        "stub_adapters": stubs,
        "stub_count": len(stubs),
        "requires_f11_work": [
            {"capability": name, "work": STUB_ADAPTER_WORK[name]} for name in stubs
        ],
        "all_known_stub_work": {},
        "quota_meter_required": True,
    }


def attach_mcp_discovery(registry: AdapterRegistry, discovery: object) -> object:
    """Bind one downstream consumer to the same lifecycle as the MCP adapter."""
    if getattr(registry, "_capabilities_closed", False):
        raise PluginPlatformConfigError("cannot attach MCP discovery after registry shutdown")
    for method in ("discover", "invoke", "aclose"):
        if not callable(getattr(discovery, method, None)):
            raise PluginPlatformConfigError(
                f"MCP discovery must implement {method}()"
            )
    existing = getattr(registry, "_mcp_discovery", None)
    if existing is not None and existing is not discovery:
        raise PluginPlatformConfigError("a different MCP discovery consumer is already attached")
    setattr(registry, "_mcp_discovery", discovery)
    return discovery


async def aclose_adapter_registry(registry: AdapterRegistry) -> None:
    """Close every distinct adapter and the MCP consumer once."""
    if getattr(registry, "_capabilities_closed", False):
        return
    setattr(registry, "_capabilities_closed", True)
    seen: set[int] = set()
    first_error: Exception | None = None
    # Stop downstream calls before closing the adapters they depend on.  The
    # registry flag plus each CloseOnce makes repeated shutdown signals harmless.
    discovery = getattr(registry, "_mcp_discovery", None)
    if discovery is not None:
        seen.add(id(discovery))
        try:
            await discovery.aclose()  # type: ignore[attr-defined]
        except Exception as exc:
            if first_error is None:
                first_error = exc
    for name in WIRED_CAPABILITIES:
        try:
            adapter = registry.resolve(name)
        except (AdapterMissingError, UnknownCapabilityError):
            continue
        if id(adapter) in seen:
            continue
        seen.add(id(adapter))
        close = getattr(adapter, "aclose", None)
        if not callable(close):
            continue
        try:
            await close()
        except Exception as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise PluginPlatformConfigError(
            f"capability adapter shutdown failed ({type(first_error).__name__})"
        ) from None


__all__ = [
    "STUB_ADAPTER_NAMES", "STUB_ADAPTER_WORK", "WIRED_CAPABILITIES",
    "UnavailableAdapter", "aclose_adapter_registry", "adapter_report",
    "attach_mcp_discovery", "build_adapter_registry", "stub_names",
]
