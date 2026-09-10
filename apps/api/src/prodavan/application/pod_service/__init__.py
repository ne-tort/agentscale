"""pod_service BC — Pod runtime orchestration."""

from __future__ import annotations

from typing import Any

__all__ = [
    "PodCommand",
    "PodLifecycleEmitter",
    "PodQuery",
    "PodReconcileService",
]


def __getattr__(name: str) -> Any:
    if name == "PodCommand":
        from prodavan.application.pod_service.command import PodCommand

        return PodCommand
    if name == "PodLifecycleEmitter":
        from prodavan.application.pod_service.lifecycle_emitter import PodLifecycleEmitter

        return PodLifecycleEmitter
    if name == "PodQuery":
        from prodavan.application.pod_service.query import PodQuery

        return PodQuery
    if name == "PodReconcileService":
        from prodavan.application.pod_service.reconcile import PodReconcileService

        return PodReconcileService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
