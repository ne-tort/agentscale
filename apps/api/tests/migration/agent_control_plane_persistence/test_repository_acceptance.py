"""SQLite-backed repository acceptance tests; authored but not run during freeze."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


from prodavan.application.agent_control_plane import (
    ControlPlaneLinkage,
    ControlPlaneWriteError,
    ControlPlaneWriteStatus,
    DurableAgentControlPlane,
)
from prodavan.domain.agent.control_plane import (
    AgentRunCreate,
    AgentRunStatus,
    ApprovalDecision,
    ApprovalRequestCreate,
    ApprovalRequestStatus,
)
from prodavan.infrastructure.persistence.models.ai_keys import AiProviderKeyRow
from prodavan.infrastructure.persistence.models.agent import AgentSessionRow
from prodavan.infrastructure.persistence.models.agent_control_plane import AgentRunRow, ApprovalRequestRow
from prodavan.infrastructure.persistence.models.base import Base
from prodavan.infrastructure.persistence.models.cabinets import (
    CabinetCompanyGrantRow,
    CabinetEmployeeAssignmentRow,
    CabinetInstanceRow,
)
from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow, MembershipRow
from prodavan.infrastructure.persistence.models.projects import ProjectEmployeeAssignmentRow, ProjectRow
from prodavan.infrastructure.persistence.agent_control_plane import SqlAlchemyAgentControlPlaneRepository
from prodavan.infrastructure.persistence.agent_control_plane.repository import _event_envelope
from prodavan.infrastructure.persistence.agent_control_plane.models import (
    AgentControlPlaneAggregateRow,
    AgentControlPlaneCommandRow,
    AgentControlPlaneEnqueueIntentRow,
    AgentControlPlaneEventRow,
    AgentControlPlaneOutboxEventRow,
    AgentControlPlaneRunScopeRow,
)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
TENANT = "tenant-acp"
PROJECT = "project-acp"
ACTOR = "employee-acp"
SESSION = "session-acp"
LINKAGE = ControlPlaneLinkage(TENANT, ACTOR, PROJECT, SESSION, "trace-acp")
PERSISTENCE_TABLES = (
    CompanyRow.__table__,
    EmployeeRow.__table__,
    MembershipRow.__table__,
    CabinetInstanceRow.__table__,
    CabinetCompanyGrantRow.__table__,
    CabinetEmployeeAssignmentRow.__table__,
    ProjectRow.__table__,
    ProjectEmployeeAssignmentRow.__table__,
    AiProviderKeyRow.__table__,
    AgentSessionRow.__table__,
    AgentRunRow.__table__,
    ApprovalRequestRow.__table__,
    AgentControlPlaneRunScopeRow.__table__,
    AgentControlPlaneAggregateRow.__table__,
    AgentControlPlaneEventRow.__table__,
    AgentControlPlaneOutboxEventRow.__table__,
    AgentControlPlaneEnqueueIntentRow.__table__,
    AgentControlPlaneCommandRow.__table__,
)


@pytest.fixture
async def store():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        # Restrict schema creation to the exact tables under test and required parents.
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=PERSISTENCE_TABLES))
        await connection.exec_driver_sql(
            "CREATE TRIGGER prevent_acp_event_mutation "
            "BEFORE UPDATE ON agent_control_plane_events "
            "BEGIN SELECT RAISE(ABORT, 'agent_control_plane_events is append-only'); END"
        )
        await connection.exec_driver_sql(
            "CREATE TRIGGER prevent_acp_event_deletion "
            "BEFORE DELETE ON agent_control_plane_events "
            "BEGIN SELECT RAISE(ABORT, 'agent_control_plane_events is append-only'); END"
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        session.add_all(
            [
                CompanyRow(
                    id=TENANT,
                    name="Control Plane Test",
                    login_slug="control-plane-test",
                    status="active",
                ),
                EmployeeRow(
                    id=ACTOR,
                    login="operator@control-plane-test.local",
                    email="operator@example.test",
                    status="active",
                ),
                CabinetInstanceRow(
                    id="cabinet-acp",
                    name="Control Plane Test",
                    schema_name="control_plane_test",
                    status="active",
                ),
                CabinetEmployeeAssignmentRow(
                    id="cabinet-assignment-acp",
                    cabinet_id="cabinet-acp",
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
                cabinet_id="cabinet-acp",
                name="Control Plane Test",
                slug="control-plane-test",
                status="active",
                visibility_mode="cabinet_shared",
                workspace_key="control-plane-test",
                container_ref="control-plane-test",
            )
        )
        session.add(MembershipRow(id="membership-acp", company_id=TENANT, employee_id=ACTOR, role="operator"))
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
    yield SqlAlchemyAgentControlPlaneRepository(session_factory=factory), factory, engine
    await engine.dispose()


@pytest.fixture
def service(store):
    repository, _, _ = store
    return DurableAgentControlPlane(
        transaction=repository,
        tenant_id=TENANT,
        queue_name="agent-control-plane-test",
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_create_and_replay_are_durable_and_do_not_duplicate_event_or_queue(service, store):
    repository, factory, _ = store
    command = AgentRunCreate(
        project_id=PROJECT,
        session_id=SESSION,
        requested_by_employee_id=ACTOR,
        idempotency_key="create-run-1",
        input_payload={"prompt": "persist me"},
    )
    first = await service.submit_run(command, linkage=LINKAGE, occurred_at=NOW)
    replay = await service.submit_run(
        command, linkage=LINKAGE, occurred_at=NOW + timedelta(seconds=30)
    )

    assert first.status is ControlPlaneWriteStatus.APPLIED
    assert replay.status is ControlPlaneWriteStatus.DUPLICATE
    assert first.run == replay.run
    assert replay.event_results[0].duplicate
    async with factory() as session:
        run_count = await session.scalar(select_count(AgentRunRow))
        event_count = await session.scalar(select_count(AgentControlPlaneEventRow))
        outbox_count = await session.scalar(select_count(AgentControlPlaneOutboxEventRow))
        intent_count = await session.scalar(select_count(AgentControlPlaneEnqueueIntentRow))
        receipt = await session.get(AgentControlPlaneCommandRow, _test_scope_key(first.run.id, "agent_run.create", "create-run-1"))
    assert (run_count, event_count, outbox_count, intent_count) == (1, 1, 1, 1)
    assert receipt is not None and receipt.status == "applied"
    with pytest.raises(IntegrityError):
        async with factory() as session, session.begin():
            await repository_row_mutation(session, first.event_results[0].event_id)
    with pytest.raises(IntegrityError):
        async with factory() as session, session.begin():
            await repository_row_deletion(session, first.event_results[0].event_id)
    async with factory() as session:
        event_row = await session.get(AgentControlPlaneEventRow, first.event_results[0].event_id)
        outbox_row = await session.get(AgentControlPlaneOutboxEventRow, first.event_results[0].event_id)
        intent_row = await session.get(
            AgentControlPlaneEnqueueIntentRow,
            (first.event_results[0].event_id, "agent-control-plane-test"),
        )
    assert event_row is not None and outbox_row is not None and intent_row is not None
    persisted_event = _event_envelope(event_row).model_dump(mode="json")
    persisted_event["occurred_at"] = event_row.occurred_at.replace(tzinfo=UTC).isoformat()
    assert outbox_row.payload == persisted_event
    assert outbox_row.event_id == event_row.event_id
    assert outbox_row.aggregate_type == event_row.aggregate_type
    assert outbox_row.aggregate_id == event_row.aggregate_id
    assert outbox_row.aggregate_version == event_row.sequence
    assert outbox_row.event_type == event_row.event_type
    assert outbox_row.occurred_at == event_row.occurred_at
    assert outbox_row.idempotency_key == intent_row.deduplication_key
    page = await repository.list_events(
        tenant_id=TENANT,
        run_id=first.run.id,
        aggregate_type="agent_run",
        aggregate_id=first.run.id,
    )
    assert [item.sequence for item in page] == [1]


async def repository_row_mutation(session, event_id):
    row = await session.get(AgentControlPlaneEventRow, event_id)
    row.payload = {"tampered": True}


async def repository_row_deletion(session, event_id):
    row = await session.get(AgentControlPlaneEventRow, event_id)
    await session.delete(row)


@pytest.mark.asyncio
async def test_same_key_with_changed_immutable_input_conflicts_without_overwriting_receipt(service, store):
    _, factory, _ = store
    first = await service.submit_run(
        AgentRunCreate(project_id=PROJECT, idempotency_key="same-key", input_payload={"v": 1}),
        linkage=ControlPlaneLinkage(TENANT, ACTOR, PROJECT, None, "trace-1"),
        occurred_at=NOW,
    )
    changed = await service.submit_run(
        AgentRunCreate(project_id=PROJECT, idempotency_key="same-key", input_payload={"v": 2}),
        linkage=ControlPlaneLinkage(TENANT, ACTOR, PROJECT, None, "trace-1"),
        occurred_at=NOW,
    )
    assert first.status is ControlPlaneWriteStatus.APPLIED
    assert changed.status is ControlPlaneWriteStatus.CONFLICT
    assert changed.reason == "idempotency_key_payload_conflict"
    async with factory() as session:
        rows = (await session.scalars(select(AgentControlPlaneCommandRow))).all()
        events = (await session.scalars(select(AgentControlPlaneEventRow))).all()
    assert len(rows) == 1 and rows[0].status == "applied"
    assert len(events) == 1 and events[0].payload["input_payload"] == {"v": 1}


@pytest.mark.asyncio
async def test_stale_aggregate_sequence_cannot_append_a_second_event(service, store):
    _, factory, _ = store
    created = await service.submit_run(
        AgentRunCreate(project_id=PROJECT, idempotency_key="cas-run"),
        linkage=ControlPlaneLinkage(TENANT, ACTOR, PROJECT, None, "trace-create"),
        occurred_at=NOW,
    )
    run_id = created.run.id
    started = await service.start_run(
        run_id=run_id,
        linkage=ControlPlaneLinkage(TENANT, ACTOR, PROJECT, None, "trace-start"),
        expected_sequence=1,
        command_idempotency_key="start-run",
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert started.status is ControlPlaneWriteStatus.APPLIED

    stale = await service.wait_for_approval(
        run_id=run_id,
        linkage=ControlPlaneLinkage(TENANT, ACTOR, PROJECT, None, "trace-stale"),
        expected_sequence=1,
        expected_status=AgentRunStatus.RUNNING,
        command_idempotency_key="wait-stale",
        occurred_at=NOW + timedelta(seconds=2),
    )
    assert stale.status is ControlPlaneWriteStatus.CONFLICT
    assert stale.reason == "aggregate_sequence_conflict"
    async with factory() as session:
        events = (
            await session.scalars(
                select(AgentControlPlaneEventRow)
                .where(AgentControlPlaneEventRow.aggregate_type == "agent_run", AgentControlPlaneEventRow.aggregate_id == run_id)
                .order_by(AgentControlPlaneEventRow.sequence)
            )
        ).all()
        cursor = await session.get(AgentControlPlaneAggregateRow, ("agent_run", run_id))
    assert [item.sequence for item in events] == [1, 2]
    assert cursor.last_sequence == 2 and cursor.status == "running"


@pytest.mark.asyncio
async def test_sequence_cursor_returns_events_for_exact_aggregate_only(service, store):
    repository, _, _ = store
    created = await service.submit_run(
        AgentRunCreate(project_id=PROJECT, idempotency_key="event-page-run"),
        linkage=ControlPlaneLinkage(TENANT, ACTOR, PROJECT, None, "trace-page"),
        occurred_at=NOW,
    )
    await service.start_run(
        run_id=created.run.id,
        linkage=ControlPlaneLinkage(TENANT, ACTOR, PROJECT, None, "trace-start"),
        expected_sequence=1,
        command_idempotency_key="event-page-start",
        occurred_at=NOW + timedelta(seconds=1),
    )
    page = await repository.list_events(
        tenant_id=TENANT,
        run_id=created.run.id,
        aggregate_type="agent_run",
        aggregate_id=created.run.id,
        after_sequence=1,
    )
    assert len(page) == 1
    assert page[0].sequence == 2 and page[0].event_type == "agent_run.started"

    with pytest.raises(ValueError, match="one aggregate"):
        await repository.list_events(tenant_id=TENANT, run_id=created.run.id, after_sequence=1)


@pytest.mark.asyncio
async def test_approval_decision_actor_and_two_aggregate_events_commit_together(service, store):
    repository, factory, _ = store
    created = await service.submit_run(
        AgentRunCreate(project_id=PROJECT, session_id=SESSION, idempotency_key="approval-run"),
        linkage=LINKAGE,
        occurred_at=NOW,
    )
    request = await service.request_approval(
        ApprovalRequestCreate(
            run_id=created.run.id,
            tool_call_id="tool-call-1",
            approval_kind="filesystem.write",
            requested_payload={"path": "/workspace/a"},
            expires_at=NOW + timedelta(minutes=5),
        ),
        linkage=LINKAGE,
        expected_run_sequence=1,
        expected_run_status=AgentRunStatus.QUEUED,
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert request.status is ControlPlaneWriteStatus.APPLIED
    request_replay = await service.request_approval(
        ApprovalRequestCreate(
            run_id=created.run.id,
            tool_call_id="tool-call-1",
            approval_kind="filesystem.write",
            requested_payload={"path": "/workspace/a"},
            expires_at=NOW + timedelta(minutes=5),
        ),
        linkage=LINKAGE,
        expected_run_sequence=1,
        expected_run_status=AgentRunStatus.QUEUED,
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert request_replay.status is ControlPlaneWriteStatus.DUPLICATE
    assert request_replay.approval == request.approval
    approval_id = request.approval.id
    decision = await service.decide_approval(
        approval_id,
        ApprovalDecision(status=ApprovalRequestStatus.APPROVED, decided_by_employee_id=ACTOR),
        run_id=created.run.id,
        linkage=LINKAGE,
        expected_approval_sequence=1,
        expected_run_sequence=2,
        occurred_at=NOW + timedelta(seconds=2),
    )
    assert decision.status is ControlPlaneWriteStatus.APPLIED
    assert decision.approval.decided_by_employee_id == ACTOR
    assert decision.approval.status is ApprovalRequestStatus.APPROVED
    assert decision.event_results[0].aggregate_type == "approval_request"
    assert decision.event_results[1].aggregate_type == "agent_run"
    before_mismatch = await repository.list_events(
        tenant_id=TENANT,
        run_id=created.run.id,
        aggregate_type="approval_request",
        aggregate_id=approval_id,
    )
    with pytest.raises(ValueError, match="authenticated linkage actor"):
        await service.decide_approval(
            approval_id,
            ApprovalDecision(status=ApprovalRequestStatus.DENIED, decided_by_employee_id="employee-other"),
            run_id=created.run.id,
            linkage=LINKAGE,
            expected_approval_sequence=1,
            expected_run_sequence=2,
            occurred_at=NOW + timedelta(seconds=3),
        )
    after_mismatch = await repository.list_events(
        tenant_id=TENANT,
        run_id=created.run.id,
        aggregate_type="approval_request",
        aggregate_id=approval_id,
    )
    assert after_mismatch == before_mismatch
    async with factory() as session:
        approval = await session.get(ApprovalRequestRow, approval_id)
        run_cursor = await session.get(AgentControlPlaneAggregateRow, ("agent_run", created.run.id))
        approval_cursor = await session.get(AgentControlPlaneAggregateRow, ("approval_request", approval_id))
        outbox = (await session.scalars(select(AgentControlPlaneOutboxEventRow))).all()
        intents = (await session.scalars(select(AgentControlPlaneEnqueueIntentRow))).all()
    assert approval.decided_by_employee_id == ACTOR and approval.decision_reason is None
    assert run_cursor.last_sequence == 3 and run_cursor.status == "running"
    assert approval_cursor.last_sequence == 2 and approval_cursor.status == "approved"
    assert len(outbox) == len(intents) == 5

    async with factory() as session, session.begin():
        employee = await session.get(EmployeeRow, ACTOR)
        await session.delete(employee)
    approval_view = await repository.get_approval(tenant_id=TENANT, approval_id=approval_id)
    assert approval_view.decided_by_employee_id == ACTOR


@pytest.mark.asyncio
async def test_wrong_tenant_project_or_session_cannot_read_or_transition_run(service, store):
    repository, _, _ = store
    created = await service.submit_run(
        AgentRunCreate(project_id=PROJECT, session_id=SESSION, idempotency_key="scoped-run"),
        linkage=LINKAGE,
        occurred_at=NOW,
    )
    wrong_tenant = await repository.get_run(tenant_id="other-tenant", run_id=created.run.id)
    assert wrong_tenant is None

    mismatch = await service.start_run(
        run_id=created.run.id,
        linkage=ControlPlaneLinkage(TENANT, ACTOR, PROJECT, None, "wrong-session"),
        expected_sequence=1,
        command_idempotency_key="wrong-session-start",
        occurred_at=NOW,
    )
    assert mismatch.status is ControlPlaneWriteStatus.CONFLICT
    assert mismatch.reason == "run_scope_not_found"

    async with store[1]() as session, session.begin():
        agent_session = await session.get(AgentSessionRow, SESSION)
        await session.delete(agent_session)
    durable_view = await repository.get_run(tenant_id=TENANT, run_id=created.run.id)
    assert durable_view.session_id == SESSION
    resumed = await service.start_run(
        run_id=created.run.id,
        linkage=LINKAGE,
        expected_sequence=1,
        command_idempotency_key="start-after-session-delete",
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert resumed.status is ControlPlaneWriteStatus.APPLIED
    assert resumed.run.session_id == SESSION


@pytest.mark.asyncio
async def test_company_membership_is_rechecked_before_transition(service, store):
    repository, factory, _ = store
    created = await service.submit_run(
        AgentRunCreate(project_id=PROJECT, idempotency_key="membership-recheck"),
        linkage=ControlPlaneLinkage(TENANT, ACTOR, PROJECT, None, "trace-membership-create"),
        occurred_at=NOW,
    )
    async with factory() as session, session.begin():
        membership = await session.scalar(
            select(MembershipRow).where(
                MembershipRow.company_id == TENANT,
                MembershipRow.employee_id == ACTOR,
            )
        )
        await session.delete(membership)

    blocked = await service.start_run(
        run_id=created.run.id,
        linkage=ControlPlaneLinkage(TENANT, ACTOR, PROJECT, None, "trace-membership-revoke"),
        expected_sequence=1,
        command_idempotency_key="membership-revoked-start",
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert blocked.status is ControlPlaneWriteStatus.BLOCKED
    events = await repository.list_events(
        tenant_id=TENANT,
        run_id=created.run.id,
        aggregate_type="agent_run",
        aggregate_id=created.run.id,
    )
    assert [item.event_type for item in events] == ["agent_run.created"]


@pytest.mark.asyncio
async def test_unrelated_project_membership_is_blocked_before_any_receipt_or_state(service, store):
    _, factory, _ = store
    result = await service.submit_run(
        AgentRunCreate(project_id="missing-project", idempotency_key="cross-project"),
        linkage=ControlPlaneLinkage(TENANT, ACTOR, "missing-project", None, "trace-cross"),
        occurred_at=NOW,
    )
    assert result.status is ControlPlaneWriteStatus.BLOCKED
    assert result.blocker == "agent_control_plane_scope_mismatch"
    async with factory() as session:
        receipts = await session.scalar(select_count(AgentControlPlaneCommandRow))
        runs = await session.scalar(select_count(AgentRunRow))
    assert receipts == runs == 0


@pytest.mark.asyncio
async def test_current_cabinet_and_restricted_project_access_are_rechecked(service, store):
    repository, factory, _ = store
    async with factory() as session, session.begin():
        project = await session.get(ProjectRow, PROJECT)
        project.visibility_mode = "restricted"

    blocked = await service.submit_run(
        AgentRunCreate(project_id=PROJECT, idempotency_key="restricted-project"),
        linkage=ControlPlaneLinkage(TENANT, ACTOR, PROJECT, None, "trace-restricted"),
        occurred_at=NOW,
    )
    assert blocked.status is ControlPlaneWriteStatus.BLOCKED
    assert blocked.blocker == "agent_control_plane_scope_mismatch"

    async with factory() as session, session.begin():
        session.add(
            ProjectEmployeeAssignmentRow(
                id="project-assignment-acp",
                project_id=PROJECT,
                cabinet_id="cabinet-acp",
                employee_id=ACTOR,
                status="active",
            )
        )
    allowed = await service.submit_run(
        AgentRunCreate(project_id=PROJECT, idempotency_key="restricted-project"),
        linkage=ControlPlaneLinkage(TENANT, ACTOR, PROJECT, None, "trace-restricted"),
        occurred_at=NOW,
    )
    assert allowed.status is ControlPlaneWriteStatus.APPLIED

    async with factory() as session, session.begin():
        assignment = await session.get(CabinetEmployeeAssignmentRow, "cabinet-assignment-acp")
        await session.delete(assignment)
    denied_transition = await service.start_run(
        run_id=allowed.run.id,
        linkage=ControlPlaneLinkage(TENANT, ACTOR, PROJECT, None, "trace-revoked"),
        expected_sequence=1,
        command_idempotency_key="after-cabinet-revoke",
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert denied_transition.status is ControlPlaneWriteStatus.BLOCKED
    assert await repository.get_run(tenant_id=TENANT, run_id=allowed.run.id) is not None


@pytest.mark.asyncio
async def test_queue_intent_failure_rolls_back_run_event_receipt_and_outbox(service, store):
    _, factory, engine = store
    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            "CREATE TRIGGER fail_acp_enqueue BEFORE INSERT ON agent_control_plane_enqueue_intents "
            "BEGIN SELECT RAISE(ABORT, 'injected queue intent failure'); END"
        )
    with pytest.raises(ControlPlaneWriteError) as error:
        await service.submit_run(
            AgentRunCreate(project_id=PROJECT, idempotency_key="atomic-rollback"),
            linkage=ControlPlaneLinkage(TENANT, ACTOR, PROJECT, None, "trace-rollback"),
            occurred_at=NOW,
        )
    assert error.value.operation == "agent_run.create"
    assert isinstance(error.value.cause, IntegrityError)
    async with factory() as session:
        counts = (
            await session.scalar(select_count(AgentRunRow)),
            await session.scalar(select_count(AgentControlPlaneRunScopeRow)),
            await session.scalar(select_count(AgentControlPlaneAggregateRow)),
            await session.scalar(select_count(AgentControlPlaneEventRow)),
            await session.scalar(select_count(AgentControlPlaneOutboxEventRow)),
            await session.scalar(select_count(AgentControlPlaneEnqueueIntentRow)),
            await session.scalar(select_count(AgentControlPlaneCommandRow)),
        )
    assert counts == (0, 0, 0, 0, 0, 0, 0)


def select_count(model):
    from sqlalchemy import func, select

    return select(func.count()).select_from(model)


def _test_scope_key(run_id: str, operation: str, key: str) -> str:
    import hashlib
    import json

    identity = {
        "tenant": TENANT,
        "project": PROJECT,
        "operation": operation,
        "run_id": run_id,
        "approval_id": None,
        "aggregates": [("agent_run", run_id)],
        "key": key,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
