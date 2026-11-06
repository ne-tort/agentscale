"""PostgreSQL-backed, tenant-scoped Agent Run reads with keyset cursors.

This query service does not dispatch runs or claim they are executing. Durable
writes and execution remain unavailable until host wiring supplies a dispatcher
and fenced provider worker.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.agent.control_plane import EventEnvelope

EXECUTION_BLOCKERS = {
    "model_registration": "agent_control_plane_models_not_registered",
    "migration": "agent_control_plane_database_not_ready",
    # The dispatcher and its durable consumer now exist; this code is only
    # reported when the consumer cannot be composed at all, so a committed
    # intent is never left without a potential claimer.
    "enqueue_dispatch": "agent_control_plane_dispatcher_unavailable",
    "run_claim": "agent_run_durable_claim_and_lease_unwired",
    "provider_unknown_outcome": "agent_run_provider_outcome_reconciliation_unwired",
}


class AgentRunPersistenceBlocked(RuntimeError):
    """A read found missing/inconsistent durable control-plane state."""

    def __init__(self, blocker: str, detail: str) -> None:
        self.blocker = blocker
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class RunPageItem:
    run: Any
    sequence: int
    session_id: str | None


@dataclass(frozen=True, slots=True)
class AgentRunPage:
    items: tuple[RunPageItem, ...]
    has_more: bool
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class ApprovalPageItem:
    approval: Any
    run: Any
    scope: Any
    approval_sequence: int
    run_sequence: int


@dataclass(frozen=True, slots=True)
class ApprovalPage:
    items: tuple[ApprovalPageItem, ...]
    has_more: bool
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class EventPage:
    items: tuple[EventEnvelope, ...]
    has_more: bool
    next_cursor: str | None


class AgentRunsQueryService:
    """Read-only database queries for employee run and timeline endpoints."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_run(self, *, tenant_id: str, project_id: str, run_id: str):
        from prodavan.infrastructure.persistence.agent_control_plane.models import (
            AgentControlPlaneAggregateRow,
            AgentControlPlaneRunScopeRow,
        )
        from prodavan.infrastructure.persistence.models.agent_control_plane import AgentRunRow

        row = (
            await self._session.execute(
                select(
                    AgentRunRow,
                    AgentControlPlaneAggregateRow.last_sequence,
                    AgentControlPlaneRunScopeRow.session_id,
                )
                .join(AgentControlPlaneRunScopeRow, AgentControlPlaneRunScopeRow.run_id == AgentRunRow.id)
                .outerjoin(
                    AgentControlPlaneAggregateRow,
                    and_(
                        AgentControlPlaneAggregateRow.aggregate_type == "agent_run",
                        AgentControlPlaneAggregateRow.aggregate_id == AgentRunRow.id,
                    ),
                )
                .where(
                    AgentControlPlaneRunScopeRow.tenant_id == tenant_id,
                    AgentControlPlaneRunScopeRow.project_id == project_id,
                    AgentRunRow.id == run_id,
                )
            )
        ).one_or_none()
        if row is None:
            return None
        run, sequence, session_id = row
        if sequence is None:
            raise AgentRunPersistenceBlocked(
                "agent_control_plane_aggregate_cursor_missing",
                "run aggregate cursor is missing",
            )
        return RunPageItem(run=run, sequence=sequence, session_id=session_id)

    async def list_runs(
        self,
        *,
        tenant_id: str,
        project_id: str,
        limit: int,
        offset: int = 0,
        cursor: str | None = None,
    ) -> AgentRunPage:
        after = decode_cursor(
            cursor,
            expected={"kind": "run", "tenant": tenant_id, "project": project_id},
        ) if cursor else None
        from prodavan.infrastructure.persistence.agent_control_plane.models import (
            AgentControlPlaneAggregateRow,
            AgentControlPlaneRunScopeRow,
        )
        from prodavan.infrastructure.persistence.models.agent_control_plane import AgentRunRow

        query = (
            select(
                AgentRunRow,
                AgentControlPlaneAggregateRow.last_sequence,
                AgentControlPlaneRunScopeRow.session_id,
            )
            .join(AgentControlPlaneRunScopeRow, AgentControlPlaneRunScopeRow.run_id == AgentRunRow.id)
            .outerjoin(
                AgentControlPlaneAggregateRow,
                and_(
                    AgentControlPlaneAggregateRow.aggregate_type == "agent_run",
                    AgentControlPlaneAggregateRow.aggregate_id == AgentRunRow.id,
                ),
            )
            .where(
                AgentControlPlaneRunScopeRow.tenant_id == tenant_id,
                AgentControlPlaneRunScopeRow.project_id == project_id,
            )
        )
        if after is not None:
            created_at = _parse_datetime(after.get("created_at"))
            run_id = _required_cursor_text(after, "id")
            query = query.where(
                or_(
                    AgentRunRow.created_at < created_at,
                    and_(AgentRunRow.created_at == created_at, AgentRunRow.id < run_id),
                )
            )
        elif offset:
            query = query.offset(offset)
        rows = (
            await self._session.execute(
                query.order_by(AgentRunRow.created_at.desc(), AgentRunRow.id.desc()).limit(limit + 1)
            )
        ).all()
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        if any(sequence is None for _, sequence, _ in page_rows):
            raise AgentRunPersistenceBlocked(
                "agent_control_plane_aggregate_cursor_missing",
                "one or more Agent Run rows have no durable aggregate cursor",
            )
        items = tuple(
            RunPageItem(run=row, sequence=sequence, session_id=session_id)
            for row, sequence, session_id in page_rows
        )
        next_cursor = None
        if has_more and page_rows:
            last_run = page_rows[-1][0]
            next_cursor = encode_cursor(
                {
                    "kind": "run",
                    "tenant": tenant_id,
                    "project": project_id,
                    "created_at": last_run.created_at.isoformat(),
                    "id": last_run.id,
                }
            )
        return AgentRunPage(items=items, has_more=has_more, next_cursor=next_cursor)

    async def list_events(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        limit: int,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        after_sequence: int | None = None,
        cursor: str | None = None,
    ) -> EventPage:
        if (aggregate_type is None) != (aggregate_id is None):
            raise ValueError("aggregate_type and aggregate_id must be supplied together")
        cursor_scope = {
            "kind": "event",
            "tenant": tenant_id,
            "project": project_id,
            "run": run_id,
            "filter_aggregate_type": aggregate_type,
            "filter_aggregate_id": aggregate_id,
        }
        after = decode_cursor(cursor, expected=cursor_scope) if cursor else None
        from prodavan.infrastructure.persistence.agent_control_plane.models import (
            AgentControlPlaneEventRow,
            AgentControlPlaneRunScopeRow,
        )

        if cursor and after_sequence is not None:
            raise ValueError("cursor and after_sequence cannot be combined")
        if after_sequence is not None and (aggregate_type is None or aggregate_id is None):
            raise ValueError("after_sequence requires aggregate_type and aggregate_id")

        query = (
            select(AgentControlPlaneEventRow)
            .join(
                AgentControlPlaneRunScopeRow,
                AgentControlPlaneRunScopeRow.run_id == AgentControlPlaneEventRow.run_id,
            )
            .where(
                AgentControlPlaneRunScopeRow.tenant_id == tenant_id,
                AgentControlPlaneRunScopeRow.project_id == project_id,
                AgentControlPlaneEventRow.tenant_id == tenant_id,
                AgentControlPlaneEventRow.project_id == project_id,
                AgentControlPlaneEventRow.run_id == run_id,
            )
        )
        if aggregate_type is not None:
            query = query.where(AgentControlPlaneEventRow.aggregate_type == aggregate_type)
        if aggregate_id is not None:
            query = query.where(AgentControlPlaneEventRow.aggregate_id == aggregate_id)
        if after_sequence is not None:
            query = query.where(AgentControlPlaneEventRow.sequence > after_sequence)
        if after is not None:
            event_aggregate_type = _required_cursor_text(after, "aggregate_type")
            event_aggregate_id = _required_cursor_text(after, "aggregate_id")
            sequence = _required_cursor_int(after, "sequence")
            event_id = _required_cursor_text(after, "event_id")
            query = query.where(
                or_(
                    AgentControlPlaneEventRow.aggregate_type > event_aggregate_type,
                    and_(
                        AgentControlPlaneEventRow.aggregate_type == event_aggregate_type,
                        AgentControlPlaneEventRow.aggregate_id > event_aggregate_id,
                    ),
                    and_(
                        AgentControlPlaneEventRow.aggregate_type == event_aggregate_type,
                        AgentControlPlaneEventRow.aggregate_id == event_aggregate_id,
                        AgentControlPlaneEventRow.sequence > sequence,
                    ),
                    and_(
                        AgentControlPlaneEventRow.aggregate_type == event_aggregate_type,
                        AgentControlPlaneEventRow.aggregate_id == event_aggregate_id,
                        AgentControlPlaneEventRow.sequence == sequence,
                        AgentControlPlaneEventRow.event_id > event_id,
                    ),
                )
            )
        rows = (
            await self._session.scalars(
                query.order_by(
                    AgentControlPlaneEventRow.aggregate_type,
                    AgentControlPlaneEventRow.aggregate_id,
                    AgentControlPlaneEventRow.sequence,
                    AgentControlPlaneEventRow.event_id,
                ).limit(limit + 1)
            )
        ).all()
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        items = tuple(
            EventEnvelope.model_validate(
                {
                    "event_id": row.event_id,
                    "event_type": row.event_type,
                    "schema_version": row.schema_version,
                    "occurred_at": row.occurred_at,
                    "producer": row.producer,
                    "aggregate_type": row.aggregate_type,
                    "aggregate_id": row.aggregate_id,
                    "run_id": row.run_id,
                    "session_id": row.session_id,
                    "correlation_id": row.correlation_id,
                    "causation_id": row.causation_id,
                    "idempotency_key": row.idempotency_key,
                    "sequence": row.sequence,
                    "payload": row.payload,
                }
            )
            for row in page_rows
        )
        next_cursor = None
        if has_more and page_rows:
            row = page_rows[-1]
            next_cursor = encode_cursor(
                {
                    **cursor_scope,
                    "occurred_at": row.occurred_at.isoformat(),
                    "aggregate_type": row.aggregate_type,
                    "aggregate_id": row.aggregate_id,
                    "sequence": row.sequence,
                    "event_id": row.event_id,
                }
            )
        return EventPage(items=items, has_more=has_more, next_cursor=next_cursor)


def encode_cursor(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def decode_cursor(cursor: str, *, expected: dict[str, Any]) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(raw)
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise ValueError("cursor is malformed") from exc
    if not isinstance(payload, dict) or any(payload.get(key) != value for key, value in expected.items()):
        raise ValueError("cursor does not match this tenant/project/run query")
    return payload


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("cursor timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("cursor timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("cursor timestamp must be timezone-aware")
    return parsed


def _required_cursor_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"cursor {key} is missing")
    return value


def _required_cursor_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"cursor {key} is invalid")
    return value


__all__ = [
    "AgentRunPage",
    "AgentRunPersistenceBlocked",
    "AgentRunsQueryService",
    "ApprovalPage",
    "ApprovalPageItem",
    "EventPage",
    "EXECUTION_BLOCKERS",
    "RunPageItem",
    "decode_cursor",
    "encode_cursor",
]
