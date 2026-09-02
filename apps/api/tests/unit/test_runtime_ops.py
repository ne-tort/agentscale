"""Unit — project runtime operation matrix."""

from __future__ import annotations

from prodavan.domain.pods import PodDesiredState
from prodavan.domain.projects.runtime_ops import (
    ProjectRuntimeOp,
    SessionRuntimeAction,
    effect_for_op,
    effect_for_stop,
    op_for_reason,
)


def test_pause_effect_suspends_sessions() -> None:
    effect = effect_for_op(ProjectRuntimeOp.PAUSE)
    assert effect.pod_desired == PodDesiredState.ABSENT
    assert effect.materialize is False
    assert effect.session_action == SessionRuntimeAction.SUSPEND


def test_resume_effect_reactivates_and_bootstraps() -> None:
    effect = effect_for_op(ProjectRuntimeOp.RESUME)
    assert effect.pod_desired == PodDesiredState.RUNNING
    assert effect.session_action == SessionRuntimeAction.REACTIVATE
    assert effect.bootstrap_sessions is True


def test_sync_materializes_without_session_change() -> None:
    effect = effect_for_op(ProjectRuntimeOp.SYNC)
    assert effect.materialize is True
    assert effect.session_action == SessionRuntimeAction.NONE
    assert effect.bootstrap_sessions is True


def test_complete_effect_suspends_sessions() -> None:
    effect = effect_for_op(ProjectRuntimeOp.COMPLETE)
    assert effect.session_action == SessionRuntimeAction.SUSPEND


def test_delete_archive_effect_suspends() -> None:
    effect = effect_for_stop(ProjectRuntimeOp.DELETE, purge_workspace=False)
    assert effect.session_action == SessionRuntimeAction.SUSPEND


def test_delete_purge_effect_cancels() -> None:
    effect = effect_for_stop(ProjectRuntimeOp.DELETE, purge_workspace=True)
    assert effect.session_action == SessionRuntimeAction.CANCEL


def test_purge_effect_cancels() -> None:
    effect = effect_for_op(ProjectRuntimeOp.PURGE)
    assert effect.session_action == SessionRuntimeAction.CANCEL


def test_op_for_reason_maps_stop_ops() -> None:
    assert op_for_reason("pause") == ProjectRuntimeOp.PAUSE
    assert op_for_reason("complete") == ProjectRuntimeOp.COMPLETE
    assert op_for_reason("purge") == ProjectRuntimeOp.PURGE
