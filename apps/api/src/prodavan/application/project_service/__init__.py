"""In-process BC: project aggregate, lifecycle, pod delegation."""

from __future__ import annotations

from typing import Any

__all__ = [
    "ProjectAccessPolicy",
    "ProjectCommand",
    "ProjectIdlePauseService",
    "ProjectLifecycleEmitter",
    "ProjectQuery",
]


def __getattr__(name: str) -> Any:
    if name == "ProjectAccessPolicy":
        from prodavan.application.project_service.access import ProjectAccessPolicy

        return ProjectAccessPolicy
    if name == "ProjectCommand":
        from prodavan.application.project_service.command import ProjectCommand

        return ProjectCommand
    if name == "ProjectIdlePauseService":
        from prodavan.application.project_service.idle_pause import ProjectIdlePauseService

        return ProjectIdlePauseService
    if name == "ProjectLifecycleEmitter":
        from prodavan.application.project_service.lifecycle_emitter import ProjectLifecycleEmitter

        return ProjectLifecycleEmitter
    if name == "ProjectQuery":
        from prodavan.application.project_service.query import ProjectQuery

        return ProjectQuery
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
