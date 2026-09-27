"""Project runtime operation effects matrix (L07)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from prodavan.domain.pods import PodDesiredState


class ProjectRuntimeOp(StrEnum):
    LAUNCH = "launch"
    PAUSE = "pause"
    RESUME = "resume"
    RELOAD = "reload"
    SYNC = "sync"
    COMPLETE = "complete"
    DELETE = "delete"
    PURGE = "purge"


class SessionRuntimeAction(StrEnum):
    NONE = "none"
    SUSPEND = "suspend"
    REACTIVATE = "reactivate"
    CANCEL = "cancel"
    PURGE = "purge"


@dataclass(frozen=True, slots=True)
class ProjectRuntimeEffect:
    pod_desired: PodDesiredState | None
    materialize: bool
    session_action: SessionRuntimeAction
    bootstrap_sessions: bool = False


RUNTIME_OP_EFFECTS: dict[ProjectRuntimeOp, ProjectRuntimeEffect] = {
    ProjectRuntimeOp.LAUNCH: ProjectRuntimeEffect(
        pod_desired=PodDesiredState.RUNNING,
        materialize=True,
        session_action=SessionRuntimeAction.NONE,
        # Launch (re)creates the pod runtime: existing ACTIVE sessions
        # must be re-registered on it, same as resume/reload.
        bootstrap_sessions=True,
    ),
    ProjectRuntimeOp.PAUSE: ProjectRuntimeEffect(
        pod_desired=PodDesiredState.ABSENT,
        materialize=False,
        session_action=SessionRuntimeAction.SUSPEND,
    ),
    ProjectRuntimeOp.RESUME: ProjectRuntimeEffect(
        pod_desired=PodDesiredState.RUNNING,
        materialize=False,
        session_action=SessionRuntimeAction.REACTIVATE,
        bootstrap_sessions=True,
    ),
    ProjectRuntimeOp.RELOAD: ProjectRuntimeEffect(
        pod_desired=PodDesiredState.RUNNING,
        materialize=False,
        session_action=SessionRuntimeAction.REACTIVATE,
        bootstrap_sessions=True,
    ),
    ProjectRuntimeOp.SYNC: ProjectRuntimeEffect(
        pod_desired=PodDesiredState.RUNNING,
        materialize=True,
        session_action=SessionRuntimeAction.NONE,
        bootstrap_sessions=True,
    ),
    ProjectRuntimeOp.COMPLETE: ProjectRuntimeEffect(
        pod_desired=PodDesiredState.ABSENT,
        materialize=False,
        session_action=SessionRuntimeAction.SUSPEND,
    ),
    ProjectRuntimeOp.PURGE: ProjectRuntimeEffect(
        pod_desired=PodDesiredState.ABSENT,
        materialize=False,
        session_action=SessionRuntimeAction.CANCEL,
    ),
}

_REASON_TO_OP: dict[str, ProjectRuntimeOp] = {
    "pause": ProjectRuntimeOp.PAUSE,
    "complete": ProjectRuntimeOp.COMPLETE,
    "delete": ProjectRuntimeOp.DELETE,
    "purge": ProjectRuntimeOp.PURGE,
    "resume": ProjectRuntimeOp.RESUME,
    "reload": ProjectRuntimeOp.RELOAD,
    "sync": ProjectRuntimeOp.SYNC,
    "rematerialize": ProjectRuntimeOp.SYNC,
    "launch": ProjectRuntimeOp.LAUNCH,
}


def op_for_reason(reason: str | None) -> ProjectRuntimeOp:
    if not reason:
        return ProjectRuntimeOp.PAUSE
    return _REASON_TO_OP.get(reason.strip().lower(), ProjectRuntimeOp.PAUSE)


def effect_for_op(op: ProjectRuntimeOp) -> ProjectRuntimeEffect:
    return RUNTIME_OP_EFFECTS[op]


def effect_for_stop(op: ProjectRuntimeOp, *, purge_workspace: bool = False) -> ProjectRuntimeEffect:
    """Session action for stop-runtime ops that depend on workspace retention."""
    if op == ProjectRuntimeOp.DELETE:
        action = (
            SessionRuntimeAction.CANCEL if purge_workspace else SessionRuntimeAction.SUSPEND
        )
        return ProjectRuntimeEffect(
            pod_desired=PodDesiredState.ABSENT,
            materialize=False,
            session_action=action,
        )
    return effect_for_op(op)
