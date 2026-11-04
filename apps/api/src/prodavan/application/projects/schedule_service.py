"""Project-owned cron schedules that enqueue durable ``system.schedule`` triggers."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.subscription_gate import CompanySubscriptionGate
from prodavan.domain.errors import AppError
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.projects import ProjectRow, ProjectScheduleRow

_FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
_FIELD_NAMES = (
    None,
    None,
    None,
    {name: index for index, name in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)},
    {name: index for index, name in enumerate(("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"))},
)
_CRON_RE = re.compile(r"^[A-Za-z0-9*/?,\-]+(?:\s+[A-Za-z0-9*/?,\-]+){4}$")
# Keep a single coalesced trigger from monopolizing the API worker after a
# prolonged outage. The cursor is still advanced exactly past ``now`` below.
_MAX_CATCH_UP_OCCURRENCES = 10_000


def _validation(detail: str) -> AppError:
    return AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail=detail)


def _parse_field(
    raw: str,
    minimum: int,
    maximum: int,
    names: dict[str, int] | None = None,
    *,
    allow_question: bool = False,
) -> set[int]:
    values: set[int] = set()

    def number(token: str) -> int:
        try:
            return int(token)
        except ValueError:
            if names is not None and token.upper() in names:
                return names[token.upper()]
            raise _validation("invalid cron value") from None

    for part in raw.split(","):
        if not part:
            raise _validation("cron field is empty")
        # Quartz-compatible ``?`` means "no restriction" only as the sole
        # value in either day field.
        if part == "?":
            if not allow_question or len(raw.split(",")) != 1:
                raise _validation("cron '?' is only allowed alone in a day field")
            values.update(range(minimum, maximum + 1))
            continue
        if "?" in part:
            raise _validation("cron '?' is only allowed alone in a day field")
        if "/" in part:
            if part.count("/") != 1:
                raise _validation("invalid cron step")
            base, step_raw = part.split("/", 1)
            try:
                step = int(step_raw)
            except ValueError as exc:
                raise _validation("invalid cron step") from exc
            if step < 1:
                raise _validation("cron step must be positive")
        else:
            base, step = part, 1
        if base in ("", "*"):
            start, end = minimum, maximum
        elif "-" in base:
            if base.count("-") != 1:
                raise _validation("invalid cron range")
            left, right = base.split("-", 1)
            start, end = number(left), number(right)
        else:
            start = end = number(base)
        if start < minimum or end > maximum or start > end:
            raise _validation("cron value out of range")
        values.update(range(start, end + 1, step))
    if not values:
        raise _validation("cron field is empty")
    return values


def validate_cron(expression: str) -> str:
    value = " ".join((expression or "").strip().split())
    if not _CRON_RE.fullmatch(value):
        raise _validation("cron must contain five standard fields")
    fields = value.split()
    if fields[2] == "?" and fields[4] == "?":
        raise _validation("cron '?' cannot be used in both day fields")
    for index, (raw, (minimum, maximum)) in enumerate(zip(fields, _FIELD_RANGES)):
        _parse_field(
            raw,
            minimum,
            maximum,
            _FIELD_NAMES[index],
            allow_question=index in (2, 4),
        )
    return value


def next_cron_at(expression: str, after: datetime, timezone: str = "UTC") -> datetime:
    """Find the next real UTC minute matching the schedule's local wall clock.

    Absolute-time iteration skips nonexistent spring-forward wall times and
    preserves both real occurrences of a matching fall-back wall time.
    """
    expression = validate_cron(expression)
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise _validation(f"unknown timezone: {timezone}") from exc
    fields = [
        _parse_field(
            raw,
            *bounds,
            _FIELD_NAMES[index],
            allow_question=index in (2, 4),
        )
        for index, (raw, bounds) in enumerate(zip(expression.split(), _FIELD_RANGES))
    ]
    candidate_utc = after.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
    dom_raw, dow_raw = expression.split()[2], expression.split()[4]
    # Five years bounds pathological sparse expressions while retaining a useful error.
    for _ in range(60 * 24 * 366 * 5):
        candidate = candidate_utc.astimezone(zone)
        month, day = candidate.month, candidate.day
        weekday = (candidate.weekday() + 1) % 7
        dom_match = day in fields[2]
        dow_match = weekday in fields[4]
        if dom_raw == "?":
            day_match = dow_match
        elif dow_raw == "?":
            day_match = dom_match
        elif dom_raw == "*" and dow_raw == "*":
            day_match = True
        elif dom_raw == "*":
            day_match = dow_match
        elif dow_raw == "*":
            day_match = dom_match
        else:
            day_match = dom_match or dow_match
        if (
            candidate.minute in fields[0]
            and candidate.hour in fields[1]
            and day_match
            and month in fields[3]
        ):
            return candidate_utc
        candidate_utc += timedelta(minutes=1)
    raise _validation("cron has no occurrence in the next five years")


def _public(row: ProjectScheduleRow) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "name": row.name,
        "cron": row.cron,
        "timezone": row.timezone,
        "payload": row.payload,
        "enabled": row.enabled,
        "next_run_at": row.next_run_at.isoformat() if row.next_run_at else None,
        "last_run_at": row.last_run_at.isoformat() if row.last_run_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class ProjectScheduleService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _project(self, project_id: str, *, require_subscription: bool = False) -> ProjectRow:
        project = await self._session.get(ProjectRow, project_id)
        if project is None or project.status == ProjectStatus.DELETED:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Project not found")
        if require_subscription:
            await CompanySubscriptionGate(self._session).require_active(project.company_id)
        return project

    async def create(
        self, *, project_id: str, name: str, cron: str, timezone: str, payload: dict, enabled: bool = True
    ) -> dict:
        await self._project(project_id, require_subscription=True)
        cron = validate_cron(cron)
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise _validation(f"unknown timezone: {timezone}") from exc
        next_run = next_cron_at(cron, datetime.now(UTC), timezone) if enabled else None
        row = ProjectScheduleRow(
            project_id=project_id, name=name, cron=cron, timezone=timezone,
            payload=payload, enabled=enabled, next_run_at=next_run,
        )
        self._session.add(row)
        await self._session.flush()
        return _public(row)

    async def list_for_project(self, *, project_id: str) -> list[dict]:
        await self._project(project_id)
        result = await self._session.execute(
            select(ProjectScheduleRow).where(ProjectScheduleRow.project_id == project_id).order_by(ProjectScheduleRow.created_at)
        )
        return [_public(row) for row in result.scalars().all()]

    async def get_for_project(self, *, project_id: str, schedule_id: str) -> ProjectScheduleRow:
        await self._project(project_id)
        row = await self._session.get(ProjectScheduleRow, schedule_id)
        if row is None or row.project_id != project_id:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Schedule not found")
        return row

    async def update(self, *, project_id: str, schedule_id: str, values: dict) -> dict:
        row = await self.get_for_project(project_id=project_id, schedule_id=schedule_id)
        changed_cron = "cron" in values or "timezone" in values
        if "cron" in values:
            row.cron = validate_cron(values["cron"])
        if "timezone" in values:
            try:
                ZoneInfo(values["timezone"])
            except ZoneInfoNotFoundError as exc:
                raise _validation(f"unknown timezone: {values['timezone']}") from exc
            row.timezone = values["timezone"]
        for key in ("name", "payload", "enabled"):
            if key in values:
                setattr(row, key, values[key])
        if changed_cron or "enabled" in values:
            row.next_run_at = next_cron_at(row.cron, datetime.now(UTC), row.timezone) if row.enabled else None
        await self._session.flush()
        # ``updated_at`` carries ``onupdate=func.now()``, so the UPDATE expires the
        # attribute and the value only comes back from the database. Reading it in
        # ``_public`` would emit a lazy SELECT from inside a non-greenlet call,
        # which async SQLAlchemy turns into MissingGreenlet (HTTP 500). Refresh
        # explicitly: one awaited round-trip, then every column is loadable.
        await self._session.refresh(row)
        return _public(row)

    async def delete(self, *, project_id: str, schedule_id: str) -> None:
        row = await self.get_for_project(project_id=project_id, schedule_id=schedule_id)
        await self._session.delete(row)
        await self._session.flush()

    async def produce_due(self, *, limit: int = 100, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)
        result = await self._session.execute(
            select(ProjectScheduleRow)
            .join(ProjectRow, ProjectRow.id == ProjectScheduleRow.project_id)
            .where(
                ProjectScheduleRow.enabled.is_(True), ProjectScheduleRow.next_run_at <= now,
                ProjectRow.status == ProjectStatus.ACTIVE,
            )
            .order_by(ProjectScheduleRow.next_run_at).limit(max(1, min(limit, 500)))
            .with_for_update(skip_locked=True)
        )
        rows = list(result.scalars().all())
        produced = 0
        for row in rows:
            project = await self._session.get(ProjectRow, row.project_id)
            if project is None or project.status == ProjectStatus.DELETED:
                row.enabled = False
                row.next_run_at = None
                continue
            due_at = row.next_run_at or now
            missed_count = 1
            occurrence_at = due_at
            catch_up_capped = False
            while missed_count < _MAX_CATCH_UP_OCCURRENCES:
                next_due_at = next_cron_at(row.cron, occurrence_at, row.timezone)
                if next_due_at <= occurrence_at:
                    # Keep a broken/non-advancing cron cursor from wedging the producer.
                    next_due_at = next_cron_at(row.cron, now, row.timezone)
                    break
                if next_due_at > now:
                    break
                missed_count += 1
                occurrence_at = next_due_at
            else:
                # Confirm one more due occurrence before reporting a capped count.
                next_due_at = next_cron_at(row.cron, occurrence_at, row.timezone)
                if next_due_at <= now:
                    catch_up_capped = True
                    next_due_at = next_cron_at(row.cron, now, row.timezone)
            if next_due_at <= occurrence_at:
                # Keep the persisted cursor strictly beyond now even for a broken cron.
                next_due_at = next_cron_at(row.cron, now, row.timezone)
            from prodavan.application.projects.trigger_service import ProjectTriggerService
            payload = {
                **(row.payload or {}),
                "schedule_id": row.id,
                "scheduled_at": due_at.isoformat(),
                "missed_count": missed_count,
            }
            if catch_up_capped:
                payload["missed_count_capped"] = True
                payload["missed_count_lower_bound"] = _MAX_CATCH_UP_OCCURRENCES
            await ProjectTriggerService(self._session).enqueue(
                project_id=row.project_id,
                kind="system.schedule",
                payload=payload,
            )
            row.last_run_at = due_at
            row.next_run_at = next_due_at
            produced += 1
        await self._session.flush()
        return {"produced": produced, "checked": len(rows)}
