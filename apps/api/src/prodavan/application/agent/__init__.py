"""Agent runtime application layer (L08)."""

from __future__ import annotations

from typing import Any

__all__ = ["AgentSessionService", "AgentTriggerDispatcher"]

_LAZY: dict[str, tuple[str, str]] = {
    "AgentSessionService": (".session_service", "AgentSessionService"),
    "AgentTriggerDispatcher": (".trigger_dispatcher", "AgentTriggerDispatcher"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_path, attr = target
    from importlib import import_module

    mod = import_module(module_path, __name__)
    value = getattr(mod, attr)
    globals()[name] = value
    return value
