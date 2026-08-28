"""Projects runtime application layer (L07).

Triggers, attachments, materialize — submodules of project runtime.
Aggregate CRUD/lifecycle lives in ``application.project_service``.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "MaterializeResult",
    "PlatformEventService",
    "ProjectAttachmentService",
    "ProjectTriggerService",
    "get_materialize_service",
]

_LAZY: dict[str, tuple[str, str]] = {
    "MaterializeResult": (".materialize", "MaterializeResult"),
    "get_materialize_service": (".materialize", "get_materialize_service"),
    "PlatformEventService": (".platform_event_service", "PlatformEventService"),
    "ProjectAttachmentService": (".attachment_service", "ProjectAttachmentService"),
    "ProjectTriggerService": (".trigger_service", "ProjectTriggerService"),
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
