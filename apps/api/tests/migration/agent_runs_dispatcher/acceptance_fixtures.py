"""Fixtures for the durable enqueue-intent dispatcher acceptance suite.

The suite is authored under the migration verification freeze: it is written to
be runnable, but it is not executed as part of this task.

Two layers are exercised:

* ``FakeConsumer`` — an in-memory implementation of
  :class:`~prodavan.application.agent_runs.dispatcher.IntentConsumerPort` used to
  prove dispatcher *policy* (classification, fail-closed refusals, retry
  scheduling, redaction) without a database.
* ``SqliteDispatchStore`` — a real SQL schema (the exact tables from revision
  ``2026110202`` plus their parents) used to prove the durable consumer's
  *mechanics*: claim, lease, fencing, reclaim, duplicate suppression and the
  atomic outcome write.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from prodavan.application.agent_runs.dispatcher import (
    ClaimedIntent,
    IntentDispatchOutcome,
    IntentKind,
    ReservationStatus,
    ResolvedIntentTarget,
)
from prodavan.domain.agent.control_plane import AgentRunStatus
from prodavan.infrastructure.persistence.agent_control_plane.intent_consumer import (
    IntentConsumerSettings,
    SqlAlchemyIntentConsumer,
    SqlAlchemyIntentScopeResolver,
)
from prodavan.infrastructure.persistence.agent_control_plane.models import (
    AgentControlPlaneAggregateRow,
    AgentControlPlaneEnqueueIntentRow,
    AgentControlPlaneEventRow,
    AgentControlPlaneOutboxEventRow,
    AgentControlPlaneRunScopeRow,
)
from prodavan.infrastructure.persistence.models.agent import AgentSessionRow
from prodavan.infrastructure.persistence.models.agent_control_plane import (
    AgentRunRow,
    ApprovalRequestRow,
)
from prodavan.infrastructure.persistence.models.base import Base
from prodavan.infrastructure.persistence.models.cabinets import (
    CabinetCompanyGrantRow,
    CabinetEmployeeAssignmentRow,
    CabinetInstanceRow,
)
from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow, MembershipRow
from prodavan.infrastructure.persistence.models.projects import ProjectEmployeeAssignmentRow, ProjectRow


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
TENANT = "tenant-dispatch"
PROJECT = "project-dispatch"
ACTOR = "employee-dispatch"
SESSION = "session-dispatch"
RUN = "run-dispatch"
QUEUE = "agent-control-plane"
DEDUP_KEY = f"15=tenant-dispatch:10=agent_run:12=run-dispatch:1=1"

PERSISTENCE_TABLES = (
    CompanyRow.__table__,
    EmployeeRow.__table__,
    MembershipRow.__table__,
    CabinetInstanceRow.__table__,
    CabinetCompanyGrantRow.__table__,
    CabinetEmployeeAssignmentRow.__table__,
    ProjectRow.__table__,
    ProjectEmployeeAssignmentRow.__table__,
    AgentSessionRow.__table__,
    AgentRunRow.__table__,
    ApprovalRequestRow.__table__,
    AgentControlPlaneRunScopeRow.__table__,
    AgentControlPlaneAggregateRow.__table__,
    AgentControlPlaneEventRow.__table__,
    AgentControlPlaneOutboxEventRow.__table__,
    AgentControlPlaneEnqueueIntentRow.__table__,
)


@dataclass
class MutableDatabaseClock:
    """Deterministic stand-in for the database clock.

    The production clock reads ``now()`` from the database
    (:class:`SqlDatabaseClock`).  Acceptance tests need to move time without
    sleeping, so they supply this port instead — the consumer still never reads a
    process clock, only the injected one.
    """

    current: datetime = NOW

    async def now(self, session: AsyncSession) -> datetime:
        del session
        return self.current

    def advance(self, *, seconds: float) -> None:
        self.current = self.current + timedelta(seconds=seconds)


@dataclass
class SqliteDispatchStore:
    """Live SQL schema for the durable consumer."""

    engine: Any
    factory: async_sessionmaker[AsyncSession]
    clock: MutableDatabaseClock

    def consumer(
        self,
        *,
        queue_name: str = QUEUE,
        settings: IntentConsumerSettings | None = None,
    ) -> SqlAlchemyIntentConsumer:
        return SqlAlchemyIntentConsumer(
            session_factory=self.factory,
            queue_name=queue_name,
            settings=settings or IntentConsumerSettings(lease_seconds=30.0, max_attempts=3),
            clock=self.clock,
        )

    def resolver(self) -> SqlAlchemyIntentScopeResolver:
        return SqlAlchemyIntentScopeResolver(session_factory=self.factory)

    async def intent_row(self, event_id: str = "event-1", queue_name: str = QUEUE):
        async with self.factory() as session:
            return await session.get(
                AgentControlPlaneEnqueueIntentRow, (event_id, queue_name)
            )

    async def intent_rows(self):
        async with self.factory() as session:
            return (
                await session.scalars(
                    select(AgentControlPlaneEnqueueIntentRow).order_by(
                        AgentControlPlaneEnqueueIntentRow.event_id
                    )
                )
            ).all()

    async def set_intent(self, *, event_id: str = "event-1", **values: Any) -> None:
        """Force intent row state (simulates an operator action or a crash)."""
        async with self.factory() as session, session.begin():
            row = await session.get(
                AgentControlPlaneEnqueueIntentRow, (event_id, QUEUE)
            )
            if row is None:
                raise AssertionError("intent row is missing")
            for key, value in values.items():
                setattr(row, key, value)

    async def count(self, model) -> int:
        async with self.factory() as session:
            return int(await session.scalar(select(func.count()).select_from(model)) or 0)

    async def disarm_poll_thread(self) -> None:  # pragma: no cover - documentation hook
        """Kept for symmetry with the poll loop's stop contract; nothing to do."""


async def build_sqlite_store(
    *,
    now: datetime = NOW,
    event_type: str = "agent_run.created",
    aggregate_type: str = "agent_run",
    aggregate_id: str = RUN,
    sequence: int = 1,
    aggregate_status: str = "queued",
    run_status: str = "queued",
    payload: dict[str, Any] | None = None,
    attempts: int = 0,
    state: str = "pending",
    available_at: datetime | None = None,
    intent_tenant: str = TENANT,
    intent_project: str = PROJECT,
    run_id: str = RUN,
) -> SqliteDispatchStore:
    """Create the schema and seed one run, one event and one enqueue intent."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(sync, tables=PERSISTENCE_TABLES)
        )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    body = {
        "project_id": PROJECT,
        "status": aggregate_status,
        "control_plane": {
            "tenant_id": TENANT,
            "actor_id": ACTOR,
            "project_id": PROJECT,
            "session_id": SESSION,
            "trace_id": "trace-dispatch",
            "command_idempotency_key": "create-run-1",
        },
    }
    if payload is not None:
        body.update(payload)

    async with factory() as session, session.begin():
        session.add_all(
            [
                CompanyRow(
                    id=TENANT, name="Dispatch Test", login_slug="dispatch-test", status="active"
                ),
                EmployeeRow(
                    id=ACTOR, login="dispatch@test.local", email="dispatch@example.test", status="active"
                ),
                CabinetInstanceRow(
                    id="cabinet-dispatch",
                    name="Dispatch Test",
                    schema_name="dispatch_test",
                    status="active",
                ),
                CabinetEmployeeAssignmentRow(
                    id="cabinet-assignment-dispatch",
                    cabinet_id="cabinet-dispatch",
                    employee_id=ACTOR,
                    role="operator",
                    status="active",
                ),
            ]
        )
        session.add(
            ProjectRow(
                id=PROJECT,
                company_id=TENANT,
                cabinet_id="cabinet-dispatch",
                name="Dispatch Test",
                slug="dispatch-test",
                status="active",
                visibility_mode="cabinet_shared",
                workspace_key="dispatch-test",
                container_ref="dispatch-test",
            )
        )
        session.add(MembershipRow(id="membership-dispatch", company_id=TENANT, employee_id=ACTOR, role="operator"))
        session.add(
            AgentSessionRow(
                id=SESSION,
                project_id=PROJECT,
                provider="test",
                api_kind="test",
                vendor_agent_id="vendor-test",
                cwd="/workspace",
            )
        )
        session.add(
            AgentRunRow(
                id=RUN,
                project_id=PROJECT,
                session_id=SESSION,
                requested_by_employee_id=ACTOR,
                idempotency_key="create-run-1",
                status=run_status,
                input_payload={"prompt": "dispatch me"},
                attempt=1,
            )
        )
        session.add(
            AgentControlPlaneRunScopeRow(
                run_id=RUN, tenant_id=TENANT, project_id=PROJECT, session_id=SESSION
            )
        )
        session.add(
            AgentControlPlaneAggregateRow(
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                last_sequence=sequence,
                status=aggregate_status,
            )
        )
        session.add(
            AgentControlPlaneEventRow(
                event_id="event-1",
                tenant_id=TENANT,
                project_id=PROJECT,
                actor_id=ACTOR,
                trace_id="trace-dispatch",
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                sequence=sequence,
                event_type=event_type,
                schema_version=1,
                occurred_at=now - timedelta(seconds=1),
                producer="agent-control-plane",
                run_id=run_id,
                session_id=SESSION,
                correlation_id=None,
                causation_id=None,
                idempotency_key="transition-key-1",
                payload=body,
            )
        )
        session.add(
            AgentControlPlaneOutboxEventRow(
                event_id="event-1",
                tenant_id=TENANT,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                aggregate_version=sequence,
                event_type=event_type,
                payload=body,
                occurred_at=now - timedelta(seconds=1),
                idempotency_key=DEDUP_KEY,
            )
        )
        session.add(
            AgentControlPlaneEnqueueIntentRow(
                event_id="event-1",
                queue_name=QUEUE,
                tenant_id=intent_tenant,
                deduplication_key=DEDUP_KEY,
                state=state,
                attempts=attempts,
                lease_generation=0,
                available_at=available_at or (now - timedelta(seconds=30)),
            )
        )

    return SqliteDispatchStore(engine=engine, factory=factory, clock=MutableDatabaseClock(current=now))


@dataclass
class FakeConsumer:
    """In-memory IntentConsumerPort used to prove dispatcher policy only."""

    intents: list[ClaimedIntent] = field(default_factory=list)
    reservation: ReservationStatus = ReservationStatus.RESERVED
    reserve_calls: list[tuple[ClaimedIntent, IntentKind, ResolvedIntentTarget]] = field(default_factory=list)
    records: list[tuple[ClaimedIntent, IntentKind | None, IntentDispatchOutcome]] = field(
        default_factory=list
    )
    record_result: bool = True
    released: int = 0

    async def claim(self, *, owner_id: str, limit: int):
        del owner_id
        return tuple(self.intents[:limit])

    async def reserve(self, *, intent, kind, target):
        self.reserve_calls.append((intent, kind, target))
        return self.reservation

    async def record_outcome(self, *, intent, kind, target, outcome):
        del target
        self.records.append((intent, kind, outcome))
        return self.record_result

    async def release_expired(self, *, limit: int) -> int:
        del limit
        return self.released


@dataclass
class FakeResolver:
    """Scope resolver returning a fixed target (or raising a fixed violation)."""

    target: ResolvedIntentTarget | None = None
    error: Exception | None = None
    calls: int = 0

    async def resolve(self, *, intent: ClaimedIntent):
        del intent
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.target


def make_claimed_intent(
    *,
    event_type: str = "agent_run.created",
    aggregate_type: str = "agent_run",
    aggregate_id: str = RUN,
    aggregate_version: int = 1,
    tenant_id: str = TENANT,
    project_id: str = PROJECT,
    run_id: str = RUN,
    session_id: str | None = SESSION,
    payload: dict[str, Any] | None = None,
    attempts: int = 1,
    claimed_at: datetime = NOW,
    lease_seconds: float = 30.0,
    lease_owner: str = "worker-a",
    lease_token: str = "token-a",
    lease_generation: int = 1,
) -> ClaimedIntent:
    body = {
        "project_id": PROJECT,
        "status": "queued",
        "control_plane": {
            "tenant_id": TENANT,
            "actor_id": ACTOR,
            "project_id": PROJECT,
            "session_id": SESSION,
            "trace_id": "trace-dispatch",
            "command_idempotency_key": "create-run-1",
        },
    }
    if payload is not None:
        body.update(payload)
    return ClaimedIntent(
        event_id="event-1",
        queue_name=QUEUE,
        tenant_id=tenant_id,
        project_id=project_id,
        deduplication_key=DEDUP_KEY,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        run_id=run_id,
        session_id=session_id,
        occurred_at=claimed_at - timedelta(seconds=1),
        payload=body,
        lease_owner=lease_owner,
        lease_token=lease_token,
        lease_generation=lease_generation,
        attempts=attempts,
        claimed_at=claimed_at,
        lease_until=claimed_at + timedelta(seconds=lease_seconds),
    )


def make_target(
    *,
    tenant_id: str = TENANT,
    project_id: str = PROJECT,
    run_id: str = RUN,
    session_id: str | None = SESSION,
    run_status: AgentRunStatus = AgentRunStatus.QUEUED,
    aggregate_type: str = "agent_run",
    aggregate_id: str = RUN,
    aggregate_status: str = "queued",
    aggregate_sequence: int = 1,
    approval_id: str | None = None,
    approval_status: str | None = None,
) -> ResolvedIntentTarget:
    return ResolvedIntentTarget(
        tenant_id=tenant_id,
        project_id=project_id,
        run_id=run_id,
        session_id=session_id,
        run_status=run_status,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_status=aggregate_status,
        aggregate_sequence=aggregate_sequence,
        approval_id=approval_id,
        approval_status=approval_status,
    )


@pytest.fixture()
def store_factory():
    """Return a builder so each test can seed its own schema shape."""
    return build_sqlite_store
