"""Acceptance contract for revisions 2026110104 and 2026110105.

These checks are reviewed during migration and intentionally are not run in the
migration freeze. They keep the revision chain, ORM metadata, and service
idempotency surface aligned.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

from sqlalchemy import Integer

from prodavan.application.projects.platform_event_service import PlatformEventService
from prodavan.infrastructure.persistence.models import PlatformEventRow, ProjectRow


_VERSIONS = Path(__file__).parents[3] / "alembic" / "versions"


def _load_revision(filename: str):
    path = _VERSIONS / filename
    spec = importlib.util.spec_from_file_location(filename[:-3], path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resume_and_platform_event_revisions_form_one_linear_chain() -> None:
    resume = _load_revision("2026110104_project_resume_event_dedup.py")
    event = _load_revision("2026110105_platform_event_idempotency.py")

    assert resume.revision == "2026110104"
    assert resume.down_revision == "2026110103"
    assert event.revision == "2026110105"
    assert event.down_revision == resume.revision
    assert "projects" in inspect.getsource(resume.upgrade)
    assert "platform_events" not in inspect.getsource(resume.upgrade)
    assert "platform_events" in inspect.getsource(event.upgrade)


def test_resume_generation_matches_projects_orm_column() -> None:
    column = ProjectRow.__table__.c.resume_generation

    assert isinstance(column.type, Integer)
    assert column.nullable is False
    assert str(column.server_default.arg) == "0"


def test_platform_event_idempotency_matches_orm_and_service_contract() -> None:
    column = PlatformEventRow.__table__.c.idempotency_key
    constraints = PlatformEventRow.__table__.constraints

    assert column.nullable is True
    assert column.type.length == 200
    assert any(
        constraint.name == "uq_platform_events_idempotency_key"
        and tuple(constraint.columns.keys()) == ("idempotency_key",)
        for constraint in constraints
    )
    assert "idempotency_key" in inspect.signature(PlatformEventService.emit).parameters


def test_concurrent_idempotent_insert_isolated_before_replay_lookup() -> None:
    source = inspect.getsource(PlatformEventService.emit)
    # Anchor on the savepoint first, then on the conflict handler's own lookup.
    # Indexing the bare tokens from offset 0 measured the *optimistic* pre-check
    # (`select(...)` before the INSERT) against the savepoint and so compared two
    # unrelated statements; it could not distinguish the replay lookup that must
    # run after the isolated INSERT from the fast-path read that legitimately
    # precedes it. The property the contract needs is that the INSERT and its
    # flush sit inside the savepoint and that the replay lookup is in the
    # IntegrityError handler, i.e. strictly after that flush.
    savepoint = source.index("async with self._session.begin_nested()")
    add = source.index("self._session.add(row)", savepoint)
    flush = source.index("await self._session.flush()", add)
    handler = source.index("except IntegrityError:", flush)
    lookup = source.index("select(PlatformEventRow).where(", handler)

    assert savepoint < add < flush < handler < lookup
    # Exactly one isolated insert attempt: a concurrent loser cannot reach the
    # replay lookup on a transaction that has already lost its savepoint.
    assert source.count("async with self._session.begin_nested()") == 1


def test_resume_serializes_marker_check_before_generation_increment() -> None:
    from prodavan.application.project_service.command import ProjectCommand

    source = inspect.getsource(ProjectCommand.resume)
    lock = source.index("with_for_update()")
    active_check = source.index('row.status == ProjectStatus.ACTIVE')
    generation = source.index("row.resume_generation =")
    captured_generation = source.index("resume_generation = row.resume_generation")
    commit = source.index("await self._session.commit()", captured_generation)

    assert lock < active_check < generation < captured_generation < commit


def test_resume_background_rejects_stale_generation_before_runtime_work() -> None:
    from prodavan.application.project_service.command import ProjectCommand

    source = inspect.getsource(ProjectCommand._resume_background)
    guard = source.index('row.launch_phase != "resuming"')
    runtime = source.index("await cmd._pods.sync_desired(")
    recheck = source.index('select(ProjectRow).where(ProjectRow.id == project_id).with_for_update()')
    status = source.index("row.status = ProjectStatus.ACTIVE")
    task_call = inspect.getsource(ProjectCommand.resume)

    assert "resume_generation=row.resume_generation" in task_call
    assert guard < runtime < recheck < status


def test_background_resume_fences_runtime_error_before_status_write() -> None:
    from prodavan.application.project_service.command import ProjectCommand

    source = inspect.getsource(ProjectCommand._resume_background)
    warning = source.index('logger.warning("resume background failed project_id=%s: %s", project_id, exc)')
    lock = source.index("select(ProjectRow).where(ProjectRow.id == project_id).with_for_update()", warning)
    stale_check = source.index('row.resume_generation != resume_generation', lock)
    error = source.index("row.status = ProjectStatus.ERROR", stale_check)

    assert warning < lock < stale_check < error


def test_stub_resume_fences_runtime_result_and_error_before_status_write() -> None:
    from prodavan.application.project_service.command import ProjectCommand

    source = inspect.getsource(ProjectCommand.resume)
    stub = source.index('if settings.pod_runtime_mode.strip().lower() == "stub":')
    runtime = source.index("await self._pods.sync_desired(", stub)
    active_recheck = source.index("row.resume_generation != resume_generation", runtime)
    active = source.index("row.status = ProjectStatus.ACTIVE", runtime)
    error_recheck = source.index("row.resume_generation != resume_generation", active)
    error = source.index("row.status = ProjectStatus.ERROR", error_recheck)
    bootstrap_recheck = source.index("await bootstrap_project_sessions(", runtime)
    event_recheck = source.index("row.resume_generation != resume_generation", bootstrap_recheck)
    event = source.index('event_type="project.resumed"', bootstrap_recheck)

    assert runtime < active_recheck < active
    assert active < error_recheck < error
    assert bootstrap_recheck < event_recheck < event
    assert source.count("with_for_update()") >= 4


def test_sync_project_retry_syncs_running_after_error_recovery() -> None:
    from prodavan.application.project_service.command import ProjectCommand

    source = inspect.getsource(ProjectCommand.sync_project)
    recovery = source.index("if row.status == ProjectStatus.ERROR:")
    active = source.index("row.status = ProjectStatus.ACTIVE", recovery)
    post = source.index("await self._apply_post_rematerialize", active)
    clear = source.index("clear_workspace_outdated(row)", post)
    helper = inspect.getsource(ProjectCommand._apply_post_rematerialize)
    running = helper.index("PodDesiredState.RUNNING")

    assert recovery < active < post < clear
    assert "row.status = ProjectStatus.ERROR" in source[post:]
    assert "row.workspace_outdated_at = datetime.now(UTC)" in source[post:]
    assert helper.index("if row.status == ProjectStatus.ACTIVE") < running


def test_sync_project_runtime_failure_persists_error_and_retry_marker() -> None:
    from prodavan.application.project_service.command import ProjectCommand

    source = inspect.getsource(ProjectCommand.sync_project)
    runtime = source.index("project sync runtime transition failed")
    error = source.index("row.status = ProjectStatus.ERROR", runtime)
    marker = source.index("row.workspace_outdated_at = datetime.now(UTC)", error)
    commit = source.index("await self._session.commit()", marker)

    assert runtime < error < marker < commit


def test_resume_bootstrap_keeps_marker_until_dispatch_succeeds() -> None:
    from prodavan.application.project_service.command import ProjectCommand

    source = inspect.getsource(ProjectCommand.resume)
    stub = source.index('if settings.pod_runtime_mode.strip().lower() == "stub":')
    dispatch = source.index("await AgentTriggerDispatcher(self._session).dispatch_batch(", stub)
    clear = source.index('row.launch_phase = None', dispatch)
    commit_before_dispatch = source.index("await self._session.commit()", dispatch - 500)

    assert commit_before_dispatch < dispatch < clear
    assert "if row.launch_phase == \"resuming\" and row.resume_generation == resume_generation:" in source[dispatch:]

    background = inspect.getsource(ProjectCommand._resume_background)
    dispatch = background.index("await AgentTriggerDispatcher(session).dispatch_batch(")
    clear = background.index('row.launch_phase = None', dispatch)
    commit_before_dispatch = background.index("await session.commit()", dispatch - 500)

    assert commit_before_dispatch < dispatch < clear
    assert "if row.launch_phase == \"resuming\" and row.resume_generation == resume_generation:" in background[dispatch:]


def test_reconcile_dispatches_before_clearing_resume_marker() -> None:
    """The resume marker is released before the trigger drain, not after it.

    This assertion used to require dispatch before the clear. The order was
    deliberately inverted: gating the marker on the drain left an ACTIVE project
    marked 'resuming' forever whenever the owner row was missing, so every later
    reconcile pass re-ran bridge registration for a project that was not resuming.
    Reconcile is serialised by an advisory lock, so no other pass can be mid-resume
    on this project while this one commits.

    What still matters, and is asserted below: the resume event and its commit come
    first, the drain failure is isolated so it cannot abort the pass, and the
    dispatcher's own retry owns the remainder.
    """
    from prodavan.application.pod_service.reconcile import PodReconcileService

    source = inspect.getsource(PodReconcileService._recover_interrupted_resume)
    event = source.index('event_type="project.resumed"')
    commit = source.index("await self._session.commit()", event)
    # The row in scope is ``project`` here, not ``current``.
    clear = source.index("project.launch_phase = None", commit)
    dispatch = source.index(
        "await AgentTriggerDispatcher(", clear
    )

    # Durable event committed, then the marker released, then the drain.
    assert event < commit < clear < dispatch
    # A drain failure is caught, never propagated, so one project cannot abort the
    # whole reconcile pass or skip the other pods in it.
    dispatch_block = source[dispatch:]
    assert "except Exception" in dispatch_block
    assert "_dispatch_failed(dispatch)" in dispatch_block
