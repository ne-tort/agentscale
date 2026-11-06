"""Acceptance checks for project-owned cron schedules."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.projects.schedule_service import ProjectScheduleService, next_cron_at, validate_cron
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.projects import ProjectScheduleRow


@pytest.mark.asyncio
async def test_schedule_production_coalesces_missed_occurrences_and_advances_cursor() -> None:
    first_due = datetime(2026, 11, 2, 5, tzinfo=UTC)
    now = datetime(2026, 11, 2, 5, 37, tzinfo=UTC)
    row = MagicMock(spec=ProjectScheduleRow)
    row.id = "sch_acceptance"
    row.project_id = "proj_acceptance"
    row.enabled = True
    row.next_run_at = first_due
    row.last_run_at = None
    row.cron = "*/10 * * * *"
    row.timezone = "UTC"
    row.payload = {"source": "acceptance"}
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
        project_id="proj_acceptance",
        kind="system.schedule",
        payload={
            "source": "acceptance",
            "schedule_id": "sch_acceptance",
            "scheduled_at": first_due.isoformat(),
            "missed_count": 4,
        },
    )
    assert row.next_run_at == datetime(2026, 11, 2, 5, 40, tzinfo=UTC)
    assert row.next_run_at > now


@pytest.mark.asyncio
async def test_schedule_production_caps_long_backlog_and_marks_count_as_lower_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("prodavan.application.projects.schedule_service._MAX_CATCH_UP_OCCURRENCES", 3)
    first_due = datetime(2020, 1, 1, tzinfo=UTC)
    now = datetime(2026, 11, 2, 5, 37, tzinfo=UTC)
    row = MagicMock(spec=ProjectScheduleRow)
    row.id = "sch_capped"
    row.project_id = "proj_capped"
    row.enabled = True
    row.next_run_at = first_due
    row.last_run_at = None
    row.cron = "* * * * *"
    row.timezone = "UTC"
    row.payload = {"source": "acceptance"}
    result = MagicMock()
    result.scalars.return_value.all.return_value = [row]
    session = AsyncMock()
    session.execute.return_value = result
    session.get.return_value = SimpleNamespace(status=ProjectStatus.ACTIVE)
    enqueue = AsyncMock(return_value={})

    def next_occurrence(_cron: str, after: datetime, _timezone: str) -> datetime:
        return after + timedelta(minutes=1)

    with (
        patch("prodavan.application.projects.trigger_service.ProjectTriggerService.enqueue", new=enqueue),
        patch("prodavan.application.projects.schedule_service.next_cron_at", side_effect=next_occurrence) as next_cron,
    ):
        outcome = await ProjectScheduleService(session).produce_due(now=now)

    assert outcome == {"produced": 1, "checked": 1}
    enqueue.assert_awaited_once_with(
        project_id="proj_capped",
        kind="system.schedule",
        payload={
            "source": "acceptance",
            "schedule_id": "sch_capped",
            "scheduled_at": first_due.isoformat(),
            "missed_count": 3,
            "missed_count_capped": True,
            "missed_count_lower_bound": 3,
        },
    )
    assert row.next_run_at == now + timedelta(minutes=1)
    assert next_cron.call_count == 4


def test_cron_normalizes_and_calculates_next_occurrence_in_timezone() -> None:
    assert validate_cron("  */15  9-17 * * 1-5 ") == "*/15 9-17 * * 1-5"
    after = datetime(2026, 11, 2, 4, 1, tzinfo=UTC)
    assert next_cron_at("0 9 * * *", after, "Europe/Samara").isoformat() == "2026-11-02T05:00:00+00:00"


def test_invalid_cron_is_rejected() -> None:
    with pytest.raises(Exception):
        validate_cron("every hour")


def test_cron_question_mark_is_restricted_to_a_day_field() -> None:
    with pytest.raises(Exception):
        validate_cron("? 9 * * *")
    with pytest.raises(Exception):
        validate_cron("0 9 ? * ?,1")
    with pytest.raises(Exception):
        validate_cron("0 9 ? * 1-?" )
    with pytest.raises(Exception):
        validate_cron("0 9 ? * ?")


def test_cron_supports_step_and_lists() -> None:
    assert next_cron_at("0 0 1,15 * *", datetime(2026, 11, 16, tzinfo=UTC)) == datetime(
        2026, 12, 1, tzinfo=UTC
    )


def test_cron_supports_quartz_names_in_month_and_weekday_range_steps() -> None:
    assert validate_cron("0 9 ? JAN,MAR MON-FRI/2") == "0 9 ? JAN,MAR MON-FRI/2"
    assert next_cron_at("0 9 ? JAN,MAR MON-FRI/2", datetime(2026, 1, 1, 5, tzinfo=UTC), "Europe/Samara") == datetime(
        2026, 1, 2, 5, tzinfo=UTC
    )


def test_cron_question_mark_leaves_the_other_day_field_unrestricted() -> None:
    after = datetime(2026, 11, 6, 4, 1, tzinfo=UTC)
    assert next_cron_at("0 9 ? * 1-5", after, "Europe/Samara") == datetime(
        2026, 11, 6, 5, tzinfo=UTC
    )
    # Same schedule, cursor advanced past Friday 09:00 Samara local: the next
    # weekday occurrence is Monday, so the weekend is skipped. This case is about
    # the schedule's own timezone, so the tz argument belongs on this call too —
    # without it the expression is evaluated in UTC, where 09:00 UTC on the same
    # Friday is legitimately the next match and the assertion would describe a
    # Samara instant against a UTC clock.
    assert next_cron_at("0 9 ? * 1-5", datetime(2026, 11, 6, 5, tzinfo=UTC), "Europe/Samara") == datetime(
        2026, 11, 9, 5, tzinfo=UTC
    )


def test_cron_skips_nonexistent_local_minute_during_spring_forward() -> None:
    # Berlin jumps from 01:59 to 03:00 local on 2026-03-29.
    after = datetime(2026, 3, 29, 0, 55, tzinfo=UTC)
    assert next_cron_at("30 2 * * *", after, "Europe/Berlin") == datetime(
        2026, 3, 30, 0, 30, tzinfo=UTC
    )


def test_cron_fires_both_ambiguous_local_minutes_during_fall_back() -> None:
    # Berlin observes 02:30 twice, first at UTC+02 and then at UTC+01.
    first = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    second = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    assert next_cron_at("30 2 * * *", first - timedelta(minutes=1), "Europe/Berlin") == first
    assert next_cron_at("30 2 * * *", first, "Europe/Berlin") == second
