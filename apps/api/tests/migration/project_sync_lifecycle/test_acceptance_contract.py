"""Acceptance contract for explicit project sync lifecycle."""

from __future__ import annotations

import inspect

from prodavan.application.project_service.command import ProjectCommand


def test_explicit_sync_runtime_failure_is_durable_and_retryable() -> None:
    source = inspect.getsource(ProjectCommand.sync_project)

    runtime_call = source.index("await self._apply_post_rematerialize(")
    # The runtime transition is wrapped in its own handler. Anchor on the handler
    # clause itself: the earlier contract anchored on the ``PROJECT_SYNC_RUNTIME_FAILED``
    # literal and assumed the error code appears *above* the state mutation, which no
    # Python handler does — ``raise AppError(code=...)`` closes the ``except`` block,
    # after the row has been persisted. The ordering assertion below states the real
    # durability contract: mutate -> commit -> raise, marker cleared only on success.
    handler = source.index("except Exception as exc:", runtime_call)
    runtime_catch = source.index('code="PROJECT_SYNC_RUNTIME_FAILED"', handler)
    error_state = source.index("row.status = ProjectStatus.ERROR", handler)
    commit = source.index("await self._session.commit()", handler)
    clear_marker = source.index("clear_workspace_outdated(row)", runtime_catch)

    assert runtime_call < handler < error_state < commit < runtime_catch < clear_marker
    assert "row.launch_phase = None" in source[runtime_call:runtime_catch]
    # The failure is surfaced as a 502 with the shared reason code, never a bare 500.
    handler_block = source[handler:]
    assert "status=502" in handler_block


def test_background_sync_recovers_error_before_runtime_transition_and_marker_clear() -> None:
    source = inspect.getsource(ProjectCommand.rematerialize_background)

    materialize = source.index("mat = await self._sync_project_workspace(row)")
    recover = source.index("if row.status == ProjectStatus.ERROR:", materialize)
    activate = source.index("row.status = ProjectStatus.ACTIVE", recover)
    runtime = source.index("await self._apply_post_rematerialize(", activate)
    clear_marker = source.index("clear_workspace_outdated(row)", runtime)

    assert materialize < recover < activate < runtime < clear_marker


def test_explicit_sync_recovery_runs_pod_transition_before_clearing_marker() -> None:
    source = inspect.getsource(ProjectCommand.sync_project)

    recovery = source.index("if row.status == ProjectStatus.ERROR:")
    runtime_call = source.index("await self._apply_post_rematerialize(")
    clear_marker = source.index("clear_workspace_outdated(row)")
    bootstrap = source.index("schedule_bootstrap_background", clear_marker)

    assert recovery < runtime_call < clear_marker < bootstrap
    assert "row.status = ProjectStatus.ACTIVE" in source[recovery:runtime_call]
