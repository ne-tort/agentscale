"""Isolated contract tests for project schedule validation and production."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from prodavan.application.projects.schedule_service import ProjectScheduleService, next_cron_at, validate_cron
from prodavan.core.jobs import names as job_names
from prodavan.core.jobs.tasks import register_tasks
from prodavan.domain.errors import AppError
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.projects import ProjectScheduleRow


def test_cron_validation_normalizes_whitespace_and_supports_steps_lists_and_ranges() -> None:
    assert validate_cron("  */15  9-17 1,15 * 1-5 ") == "*/15 9-17 1,15 * 1-5"


@pytest.mark.parametrize("expression", ["every hour", "0 24 * * *", "0 0 32 * *", "0 0 * * 7", "0 0 */0 * *"])
def test_cron_validation_rejects_malformed_or_out_of_range_fields(expression: str) -> None:
    with pytest.raises(AppError) as error:
        validate_cron(expression)
    assert error.value.code == "VALIDATION_ERROR"
    assert error.value.status == 422


def test_next_cron_at_rejects_unknown_timezone() -> None:
    with pytest.raises(AppError, match="unknown timezone"):
        next_cron_at("0 9 * * *", datetime(2026, 11, 2, tzinfo=UTC), "Mars/Olympus")


def test_next_cron_at_returns_utc_occurrence_for_named_timezone() -> None:
    after = datetime(2026, 11, 2, 4, 1, tzinfo=UTC)
    assert next_cron_at("0 9 * * 1-5", after, "Europe/Samara") == datetime(2026, 11, 2, 5, tzinfo=UTC)


@pytest.mark.asyncio
async def test_produce_due_claim_query_skips_locked_rows_and_moves_schedule_forward() -> None:
    due = datetime(2026, 11, 2, 5, tzinfo=UTC)
    now = datetime(2026, 11, 2, 5, 3, tzinfo=UTC)
    row = MagicMock(spec=ProjectScheduleRow)
    row.id = "sch_1"
    row.project_id = "proj_1"
    row.enabled = True
    row.next_run_at = due
    row.last_run_at = None
    row.cron = "0 9 * * *"
    row.timezone = "Europe/Samara"
    row.payload = {"source": "timer"}
    result = MagicMock()
    result.scalars.return_value.all.return_value = [row]
    session = AsyncMock()
    session.execute.return_value = result
    session.get.return_value = SimpleNamespace(status=ProjectStatus.ACTIVE)
    session.flush = AsyncMock()
    trigger_result = {"id": "trg_1"}

    with patch(
        "prodavan.application.projects.trigger_service.ProjectTriggerService.enqueue",
        new=AsyncMock(return_value=trigger_result),
    ) as enqueue:
        # trigger_service is imported locally inside produce_due.
        outcome = await ProjectScheduleService(session).produce_due(now=now)

    query = session.execute.await_args.args[0]
    sql = str(query.compile(dialect=postgresql.dialect())).upper()
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert outcome == {"produced": 1, "checked": 1}
    enqueue.assert_awaited_once_with(
        project_id="proj_1",
        kind="system.schedule",
        payload={
            "source": "timer", "schedule_id": "sch_1", "scheduled_at": due.isoformat(), "missed_count": 1
        },
    )
    assert row.last_run_at == due
    assert row.next_run_at == datetime(2026, 11, 3, 5, tzinfo=UTC)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_produce_due_coalesces_all_missed_occurrences_into_one_trigger() -> None:
    first_due = datetime(2026, 11, 2, 5, tzinfo=UTC)
    now = datetime(2026, 11, 2, 5, 37, tzinfo=UTC)
    row = MagicMock(spec=ProjectScheduleRow)
    row.id = "sch_1"
    row.project_id = "proj_1"
    row.enabled = True
    row.next_run_at = first_due
    row.last_run_at = None
    row.cron = "*/10 * * * *"
    row.timezone = "UTC"
    row.payload = {"source": "timer"}
    result = MagicMock()
    result.scalars.return_value.all.return_value = [row]
    session = AsyncMock()
    session.execute.return_value = result
    session.get.return_value = SimpleNamespace(status=ProjectStatus.ACTIVE)
    enqueue = AsyncMock(return_value={})

    with patch("prodavan.application.projects.trigger_service.ProjectTriggerService.enqueue", new=enqueue):
        outcome = await ProjectScheduleService(session).produce_due(now=now)

    assert outcome == {"produced": 1, "checked": 1}
    enqueue.assert_awaited_once_with(
        project_id="proj_1",
        kind="system.schedule",
        payload={
            "source": "timer",
            "schedule_id": "sch_1",
            "scheduled_at": first_due.isoformat(),
            "missed_count": 4,
        },
    )
    assert row.last_run_at == first_due
    assert row.next_run_at == datetime(2026, 11, 2, 5, 40, tzinfo=UTC)
    assert row.next_run_at > now


@pytest.mark.asyncio
async def test_produce_due_respects_limit_bounds() -> None:
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session = AsyncMock()
    session.execute.return_value = result

    await ProjectScheduleService(session).produce_due(limit=10000)

    query = session.execute.await_args.args[0]
    assert query._limit_clause.value == 500


@pytest.mark.asyncio
async def test_produce_due_does_not_enqueue_twice_after_advancing_due_cursor() -> None:
    row = MagicMock(spec=ProjectScheduleRow)
    row.id = "sch_1"
    row.project_id = "proj_1"
    row.next_run_at = datetime(2026, 11, 2, 5, tzinfo=UTC)
    row.cron = "0 9 * * *"
    row.timezone = "Europe/Samara"
    row.payload = {}
    result = MagicMock()
    result.scalars.return_value.all.return_value = [row]
    session = AsyncMock()
    session.execute.return_value = result
    session.get.return_value = SimpleNamespace(status=ProjectStatus.ACTIVE)
    enqueue = AsyncMock(return_value={})

    session.execute.side_effect = lambda *_args, **_kwargs: result
    result.scalars.return_value.all.side_effect = lambda: (
        [row] if row.next_run_at <= datetime(2026, 11, 2, 5, 4, tzinfo=UTC) else []
    )

    with patch("prodavan.application.projects.trigger_service.ProjectTriggerService.enqueue", new=enqueue):
        first = await ProjectScheduleService(session).produce_due(now=datetime(2026, 11, 2, 5, 3, tzinfo=UTC))
        second = await ProjectScheduleService(session).produce_due(now=datetime(2026, 11, 2, 5, 4, tzinfo=UTC))

    assert first == {"produced": 1, "checked": 1}
    assert second == {"produced": 0, "checked": 0}
    assert enqueue.await_count == 1


class _TaskApp:
    # A real Celery app accepts arbitrary task options, so the double must too:
    # ``register_tasks`` legitimately registers some jobs with ``acks_late`` /
    # ``reject_on_worker_lost``. Rejecting those keywords here failed the test on
    # unrelated registrations instead of on the lock behaviour under test.
    def task(self, *, name: str, bind: bool = False, **options: object):
        self.options.append((name, options))

        def _decorate(fn):
            fn.name = name
            self.tasks[name] = fn
            return fn

        return _decorate

    def __init__(self) -> None:
        self.tasks: dict[str, object] = {}
        self.options: list[tuple[str, dict[str, object]]] = []



def test_project_schedule_producer_skips_when_distributed_lock_is_held(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _TaskApp()
    monkeypatch.setattr("prodavan.core.jobs.tasks._register_worker_k8s_bootstrap", lambda _app: None)
    monkeypatch.setattr("prodavan.core.jobs.tasks.run_async", asyncio.run)
    invoked: list[tuple[str, int]] = []

    async def _locked(job: str, *, ttl_sec: int, fn):
        invoked.append((job, ttl_sec))
        return {"ok": False, "skipped": True, "reason": "lock_held", "job": job}

    monkeypatch.setattr("prodavan.core.jobs.tasks.run_with_job_lock", _locked)
    register_tasks(app)

    task = app.tasks[job_names.PRODUCE_PROJECT_SCHEDULES]
    assert task() == {
        "ok": False,
        "skipped": True,
        "reason": "lock_held",
        "job": "produce_project_schedules",
    }
    assert invoked == [("produce_project_schedules", 60)]
