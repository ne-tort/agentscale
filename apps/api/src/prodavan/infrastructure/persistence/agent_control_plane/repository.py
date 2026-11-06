"""SQLAlchemy UoW for atomic durable agent run, approval, event and enqueue writes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.agent_control_plane.contracts import (
    ControlPlaneAtomicWrite,
    ControlPlaneWriteResult,
    ControlPlaneWriteStatus,
)
from prodavan.application.agent_control_plane.ports import AgentControlPlaneReadPort, AgentControlPlaneTransactionPort
from prodavan.domain.identity import ROLE_COMPANY_ADMIN
from prodavan.domain.agent.control_plane import (
    AgentRunStatus,
    AgentRunView,
    ApprovalRequestStatus,
    ApprovalRequestView,
    EventEnvelope,
)
from prodavan.infrastructure.persistence.models.agent import AgentSessionRow
from prodavan.infrastructure.persistence.models.agent_control_plane import AgentRunRow, ApprovalRequestRow
from prodavan.infrastructure.persistence.models.cabinets import (
    CabinetCompanyGrantRow,
    CabinetEmployeeAssignmentRow,
    CabinetInstanceRow,
)
from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow, MembershipRow
from prodavan.infrastructure.persistence.models.projects import ProjectEmployeeAssignmentRow, ProjectRow

from .models import (
    AgentControlPlaneAggregateRow,
    AgentControlPlaneCommandRow,
    AgentControlPlaneEnqueueIntentRow,
    AgentControlPlaneEventRow,
    AgentControlPlaneOutboxEventRow,
    AgentControlPlaneRunScopeRow,
)


class SqlAlchemyAgentControlPlaneRepository(AgentControlPlaneTransactionPort, AgentControlPlaneReadPort):
    """One repository-owned transaction for state, receipts, events and queue intents.

    ``session_factory`` must point to the platform database containing projects,
    memberships, agent runs and approvals. The adapter intentionally does not
    expose separate mutating repositories: one ``commit`` owns the complete UoW.
    """

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def commit(self, *, write: ControlPlaneAtomicWrite) -> ControlPlaneWriteResult:
        _validate_atomic_write(write)
        command = write.command
        write_digest = _write_digest(write)
        scope_key = _scope_key(write)

        async with self._session_factory() as session:
            async with session.begin():
                previous = await session.get(AgentControlPlaneCommandRow, scope_key)
                if not await self._valid_scope(
                    session,
                    write,
                    allow_missing_session_replay=previous is not None,
                ):
                    return _blocked("agent_control_plane_scope_mismatch")
                if previous is not None:
                    if previous.command_digest != write_digest:
                        return _conflict("idempotency_key_payload_conflict")
                    if previous.result_payload is None:
                        raise RuntimeError("committed agent control-plane command has no result receipt")
                    return _restore_result(previous.result_payload, duplicate=True)

                receipt = AgentControlPlaneCommandRow(
                    scope_key=scope_key,
                    tenant_id=command.linkage.tenant_id,
                    project_id=command.linkage.project_id,
                    operation=command.operation,
                    run_id=command.run_id,
                    approval_id=command.approval_id,
                    command_idempotency_key=command.command_idempotency_key,
                    command_digest=write_digest,
                    status="in_progress",
                )
                try:
                    async with session.begin_nested():
                        session.add(receipt)
                        await session.flush()
                except IntegrityError:
                    previous = await session.get(AgentControlPlaneCommandRow, scope_key)
                    if previous is None:
                        raise
                    if previous.command_digest != write_digest:
                        return _conflict("idempotency_key_payload_conflict")
                    if previous.result_payload is None:
                        raise RuntimeError("committed agent control-plane command has no result receipt")
                    return _restore_result(previous.result_payload, duplicate=True)

                unit = await session.begin_nested()
                try:
                    result = await self._apply(session, write)
                    if result.status is ControlPlaneWriteStatus.CONFLICT:
                        await unit.rollback()
                    else:
                        await unit.commit()
                except IntegrityError as exc:
                    await unit.rollback()
                    if not _is_unique_violation(exc):
                        raise
                    result = _conflict("atomic_constraint_conflict")
                except Exception:
                    await unit.rollback()
                    raise
                receipt.status = result.status.value
                receipt.result_payload = _result_payload(result)
                receipt.completed_at = datetime.now(UTC)
                await session.flush()
                return result

    async def get_run(self, *, tenant_id: str, run_id: str) -> AgentRunView | None:
        async with self._session_factory() as session:
            row = await session.execute(
                select(AgentRunRow, AgentControlPlaneRunScopeRow)
                .join(AgentControlPlaneRunScopeRow, AgentControlPlaneRunScopeRow.run_id == AgentRunRow.id)
                .where(
                    AgentRunRow.id == run_id,
                    AgentControlPlaneRunScopeRow.tenant_id == tenant_id,
                )
            )
            result = row.one_or_none()
            if result is None:
                return None
            failure_code = result[0].failure_code
            if failure_code is None and result[0].status == AgentRunStatus.FAILED.value:
                payload = await session.scalar(
                    select(AgentControlPlaneEventRow.payload)
                    .where(
                        AgentControlPlaneEventRow.run_id == run_id,
                        AgentControlPlaneEventRow.tenant_id == tenant_id,
                        AgentControlPlaneEventRow.project_id == result[1].project_id,
                        AgentControlPlaneEventRow.aggregate_type == "agent_run",
                        AgentControlPlaneEventRow.payload["status"].as_string() == AgentRunStatus.FAILED.value,
                        AgentControlPlaneEventRow.event_type.in_(
                            ("agent_run.failed", "agent_run.timed_out")
                        ),
                    )
                    .order_by(AgentControlPlaneEventRow.sequence.desc())
                    .limit(1)
                )
                failure_code = payload.get("failure_code") if payload else None
            return _run_view(
                result[0], session_id=result[1].session_id, failure_code=failure_code
            )

    async def get_approval(self, *, tenant_id: str, approval_id: str) -> ApprovalRequestView | None:
        async with self._session_factory() as session:
            row = await session.execute(
                select(ApprovalRequestRow, AgentControlPlaneRunScopeRow)
                .join(AgentControlPlaneRunScopeRow, AgentControlPlaneRunScopeRow.run_id == ApprovalRequestRow.run_id)
                .where(
                    ApprovalRequestRow.id == approval_id,
                    AgentControlPlaneRunScopeRow.tenant_id == tenant_id,
                )
            )
            result = row.one_or_none()
            if result is None:
                return None
            approval, scope = result
            actor_id = None
            if approval.status != ApprovalRequestStatus.PENDING.value:
                actor_id = await session.scalar(
                    select(AgentControlPlaneEventRow.actor_id)
                    .where(
                        AgentControlPlaneEventRow.aggregate_type == "approval_request",
                        AgentControlPlaneEventRow.aggregate_id == approval_id,
                        AgentControlPlaneEventRow.tenant_id == tenant_id,
                        AgentControlPlaneEventRow.project_id == scope.project_id,
                        AgentControlPlaneEventRow.event_type.in_(
                            (
                                "approval_request.approved",
                                "approval_request.denied",
                                "approval_request.cancelled",
                                "approval_request.expired",
                            )
                        ),
                    )
                    .order_by(AgentControlPlaneEventRow.sequence.desc())
                    .limit(1)
                )
            return _approval_view(approval, decided_by_employee_id=actor_id)

    async def list_events(
        self,
        *,
        tenant_id: str,
        run_id: str,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[EventEnvelope, ...]:
        """Read a bounded event page; sequence ordering is per aggregate."""
        if after_sequence < 0 or not 1 <= limit <= 1000:
            raise ValueError("event cursor or limit is invalid")
        if after_sequence and (aggregate_type is None or aggregate_id is None):
            raise ValueError("a sequence cursor must identify one aggregate type and ID")
        async with self._session_factory() as session:
            query = (
                select(AgentControlPlaneEventRow)
                .join(AgentControlPlaneRunScopeRow, AgentControlPlaneRunScopeRow.run_id == AgentControlPlaneEventRow.run_id)
                .where(
                    AgentControlPlaneRunScopeRow.tenant_id == tenant_id,
                    AgentControlPlaneEventRow.run_id == run_id,
                    AgentControlPlaneEventRow.sequence > after_sequence,
                )
            )
            if aggregate_type is not None:
                query = query.where(AgentControlPlaneEventRow.aggregate_type == aggregate_type)
            if aggregate_id is not None:
                query = query.where(AgentControlPlaneEventRow.aggregate_id == aggregate_id)
            rows = (
                await session.scalars(
                    query.order_by(
                        AgentControlPlaneEventRow.aggregate_type,
                        AgentControlPlaneEventRow.aggregate_id,
                        AgentControlPlaneEventRow.sequence,
                    ).limit(limit)
                )
            ).all()
            return tuple(_event_envelope(row) for row in rows)

    async def _apply(self, session: AsyncSession, write: ControlPlaneAtomicWrite) -> ControlPlaneWriteResult:
        command = write.command
        run = await session.get(AgentRunRow, command.run_id)

        if command.expected_run_status is None:
            if run is not None:
                return _conflict("run_identity_conflict")
            details = command.details
            run = AgentRunRow(
                id=command.run_id,
                project_id=command.linkage.project_id,
                session_id=command.linkage.session_id,
                requested_by_employee_id=details.get("requested_by_employee_id"),
                idempotency_key=str(details.get("idempotency_key", command.command_idempotency_key)),
                status=command.target_run_status.value,
                input_payload=_plain(details.get("input_payload") or {}),
                attempt=1,
            )
            session.add(run)
            # The scope row holds an immediate FK to ``agent_runs``.  The models
            # declare foreign keys but no ORM relationships, so the unit of work
            # has no dependency edge between these mappers and orders them by
            # table name — ``agent_control_plane_run_scopes`` sorts before
            # ``agent_runs`` and would be inserted first, violating the FK on
            # every engine (SQLite and PostgreSQL both reject it).  Flush the
            # parent row before the child is added.
            await session.flush()
            session.add(
                AgentControlPlaneRunScopeRow(
                    run_id=command.run_id,
                    tenant_id=command.linkage.tenant_id,
                    project_id=command.linkage.project_id,
                    session_id=command.linkage.session_id,
                )
            )
        if command.expected_run_status is not None:
            if run is None or run.status != command.expected_run_status.value:
                return _conflict("run_status_conflict")
            run_scope = await session.get(AgentControlPlaneRunScopeRow, command.run_id)
            if (
                run_scope is None
                or run.project_id != command.linkage.project_id
                or (run.session_id is not None and run.session_id != command.linkage.session_id)
                or run_scope.tenant_id != command.linkage.tenant_id
                or run_scope.project_id != command.linkage.project_id
                or run_scope.session_id != command.linkage.session_id
            ):
                return _conflict("run_scope_not_found")
            if (
                command.target_run_status in {AgentRunStatus.FAILED, AgentRunStatus.CANCELLED}
                and command.approval_id is None
            ):
                pending_approval = await session.scalar(
                    select(ApprovalRequestRow.id)
                    .where(
                        ApprovalRequestRow.run_id == command.run_id,
                        ApprovalRequestRow.status == ApprovalRequestStatus.PENDING.value,
                    )
                    .limit(1)
                )
                if pending_approval is not None:
                    return _conflict("active_approval_requires_atomic_resolution")

        approval: ApprovalRequestRow | None = None
        if command.approval_id is not None:
            approval = await session.get(ApprovalRequestRow, command.approval_id)
            if command.expected_approval_status is None:
                if approval is not None:
                    return _conflict("approval_identity_conflict")
                details = command.details
                approval = ApprovalRequestRow(
                    id=command.approval_id,
                    run_id=command.run_id,
                    tool_call_id=str(details["tool_call_id"]),
                    approval_kind=str(details["approval_kind"]),
                    status=command.target_approval_status.value,
                    requested_payload=_plain(details.get("requested_payload") or {}),
                    expires_at=_parse_datetime(details.get("expires_at")),
                )
                session.add(approval)
            else:
                if approval is None or approval.run_id != command.run_id:
                    return _conflict("approval_scope_conflict")
                if approval.status != command.expected_approval_status.value:
                    return _conflict("approval_status_conflict")

        # All aggregate cursors are claimed in stable lock order. Each UPDATE
        # includes both expected sequence and state so no read-then-write CAS is used.
        for version in sorted(write.command.aggregates, key=lambda item: (item.aggregate_type, item.aggregate_id)):
            if version.expected_sequence == 0:
                if await session.get(AgentControlPlaneAggregateRow, (version.aggregate_type, version.aggregate_id)):
                    return _conflict("aggregate_sequence_conflict")
                session.add(
                    AgentControlPlaneAggregateRow(
                        aggregate_type=version.aggregate_type,
                        aggregate_id=version.aggregate_id,
                        last_sequence=version.next_sequence,
                        status=_target_aggregate_status(write, version.aggregate_type),
                    )
                )
                continue
            expected_status = (
                command.expected_run_status.value
                if version.aggregate_type == "agent_run"
                else command.expected_approval_status.value
            )
            changed = await session.execute(
                update(AgentControlPlaneAggregateRow)
                .where(
                    AgentControlPlaneAggregateRow.aggregate_type == version.aggregate_type,
                    AgentControlPlaneAggregateRow.aggregate_id == version.aggregate_id,
                    AgentControlPlaneAggregateRow.last_sequence == version.expected_sequence,
                    AgentControlPlaneAggregateRow.status == expected_status,
                )
                .values(
                    last_sequence=version.next_sequence,
                    status=_target_aggregate_status(write, version.aggregate_type),
                    updated_at=datetime.now(UTC),
                )
            )
            if changed.rowcount != 1:
                return _conflict("aggregate_sequence_conflict")

        now = _event_time(write.events)
        run.status = command.target_run_status.value
        run.updated_at = now
        if command.target_run_status is AgentRunStatus.RUNNING and run.started_at is None:
            run.started_at = now
        if command.target_run_status in {
            AgentRunStatus.SUCCEEDED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }:
            run.finished_at = now
        details = command.details
        if details.get("output_payload") is not None:
            run.output_payload = _plain(details["output_payload"])
        failure_code_value = details.get("failure_code")
        if failure_code_value is not None:
            # Keep the legacy row's 64-character limit; the full immutable code remains in the event.
            run.failure_code = str(failure_code_value) if len(str(failure_code_value)) <= 64 else None
        if details.get("reason") is not None and command.target_run_status is AgentRunStatus.FAILED:
            run.failure_message = str(details["reason"])

        if approval is not None and command.expected_approval_status is not None:
            approval.status = command.target_approval_status.value
            approval.decided_by_employee_id = command.linkage.actor_id
            approval.decision_reason = details.get("reason")
            approval.decided_at = now
            approval.updated_at = now

        projections = tuple(
            zip(write.events, write.outbox_events, write.outbox_intents, strict=True)
        )

        # Three FK levels, written one level at a time.  The ORM has no
        # relationships between these rows, so the unit of work would order the
        # mappers by table name: ``..._enqueue_intents`` < ``..._events`` <
        # ``..._outbox_events``, i.e. every child before its parent, which fails
        # the FK on any engine.  Each level is flushed before the next is added.
        session.add_all([_event_row(write, event) for event, _, _ in projections])
        await session.flush()
        session.add_all(
            [
                AgentControlPlaneOutboxEventRow(
                    event_id=outbox_event.event_id,
                    tenant_id=outbox_event.tenant_id,
                    aggregate_type=outbox_event.aggregate_type,
                    aggregate_id=outbox_event.aggregate_id,
                    aggregate_version=outbox_event.aggregate_version,
                    event_type=outbox_event.event_type,
                    payload=_plain(outbox_event.payload),
                    occurred_at=outbox_event.occurred_at,
                    idempotency_key=outbox_event.idempotency_key,
                )
                for _, outbox_event, _ in projections
            ]
        )
        await session.flush()
        session.add_all(
            [
                AgentControlPlaneEnqueueIntentRow(
                    event_id=intent.event_id,
                    queue_name=intent.queue_name,
                    tenant_id=command.linkage.tenant_id,
                    deduplication_key=intent.deduplication_key,
                )
                for _, _, intent in projections
            ]
        )

        if command.approval_id is not None and approval is None:
            raise RuntimeError("approval write did not create or resolve its approval row")
        await session.flush()
        await session.refresh(run)
        if approval is not None:
            await session.refresh(approval)

        event_results = tuple(
            _event_result(event, outbox_event.idempotency_key, intent.deduplication_key)
            for event, outbox_event, intent in projections
        )
        return ControlPlaneWriteResult(
            status=ControlPlaneWriteStatus.APPLIED,
            event_results=event_results,
            run=_run_view(
                run,
                session_id=command.linkage.session_id,
                failure_code=details.get("failure_code"),
            ),
            approval=_approval_view(approval) if approval is not None else None,
        )

    async def _valid_scope(
        self,
        session: AsyncSession,
        write: ControlPlaneAtomicWrite,
        *,
        allow_missing_session_replay: bool = False,
    ) -> bool:
        linkage = write.command.linkage
        project = await session.scalar(
            select(ProjectRow)
            .where(ProjectRow.id == linkage.project_id)
            .with_for_update()
        )
        if project is None or project.company_id != linkage.tenant_id or project.status != "active":
            return False

        company = await session.scalar(
            select(CompanyRow)
            .where(CompanyRow.id == linkage.tenant_id)
            .with_for_update()
        )
        if company is None or company.deleted_at is not None or company.status != "active":
            return False

        employee = await session.scalar(
            select(EmployeeRow)
            .where(
                EmployeeRow.id == linkage.actor_id,
                EmployeeRow.deleted_at.is_(None),
                EmployeeRow.status == "active",
            )
            .with_for_update()
        )
        if employee is None:
            return False
        member = await session.scalar(
            select(MembershipRow)
            .where(
                MembershipRow.company_id == linkage.tenant_id,
                MembershipRow.employee_id == linkage.actor_id,
            )
            .with_for_update()
        )
        if member is None:
            return False

        cabinet = await session.scalar(
            select(CabinetInstanceRow)
            .where(CabinetInstanceRow.id == project.cabinet_id)
            .with_for_update()
        )
        if cabinet is None or cabinet.status != "active":
            return False
        cabinet_assignment = await session.scalar(
            select(CabinetEmployeeAssignmentRow.id)
            .where(
                CabinetEmployeeAssignmentRow.cabinet_id == cabinet.id,
                CabinetEmployeeAssignmentRow.employee_id == linkage.actor_id,
                CabinetEmployeeAssignmentRow.status == "active",
            )
            .with_for_update()
        )
        cabinet_grant = await session.scalar(
            select(CabinetCompanyGrantRow.id)
            .where(
                CabinetCompanyGrantRow.cabinet_id == cabinet.id,
                CabinetCompanyGrantRow.company_id == linkage.tenant_id,
                CabinetCompanyGrantRow.status == "active",
            )
            .with_for_update()
        )
        has_company_access = (
            cabinet.company_grant_scope == "all"
            or (
                cabinet.owner_scope == "company"
                and cabinet.owner_company_id == linkage.tenant_id
                and cabinet.status != "deleted"
            )
            or cabinet_grant is not None
        )
        if cabinet_assignment is None and not has_company_access:
            return False

        if project.visibility_mode == "restricted" and member.role != ROLE_COMPANY_ADMIN:
            project_assignment = await session.scalar(
                select(ProjectEmployeeAssignmentRow.id)
                .where(
                    ProjectEmployeeAssignmentRow.project_id == project.id,
                    ProjectEmployeeAssignmentRow.employee_id == linkage.actor_id,
                    ProjectEmployeeAssignmentRow.status == "active",
                )
                .with_for_update()
            )
            if project_assignment is None:
                return False

        if linkage.session_id is not None:
            agent_session = await session.scalar(
                select(AgentSessionRow)
                .where(AgentSessionRow.id == linkage.session_id)
                .with_for_update()
            )
            if agent_session is None:
                if write.command.expected_run_status is None and not allow_missing_session_replay:
                    return False
            elif agent_session.project_id != linkage.project_id:
                return False
        return True

    async def _scoped_run(
        self, session: AsyncSession, write: ControlPlaneAtomicWrite
    ) -> tuple[AgentRunRow | None, AgentControlPlaneRunScopeRow | None]:
        command = write.command
        row = (
            await session.execute(
                select(AgentRunRow, AgentControlPlaneRunScopeRow)
                .join(AgentControlPlaneRunScopeRow, AgentControlPlaneRunScopeRow.run_id == AgentRunRow.id)
                .where(
                    AgentRunRow.id == command.run_id,
                    AgentRunRow.project_id == command.linkage.project_id,
                    AgentRunRow.session_id.is_(None)
                    if command.linkage.session_id is None
                    else AgentRunRow.session_id == command.linkage.session_id,
                    AgentControlPlaneRunScopeRow.tenant_id == command.linkage.tenant_id,
                    AgentControlPlaneRunScopeRow.project_id == command.linkage.project_id,
                    AgentControlPlaneRunScopeRow.session_id.is_(None)
                    if command.linkage.session_id is None
                    else AgentControlPlaneRunScopeRow.session_id == command.linkage.session_id,
                )
            )
        ).one_or_none()
        return (row[0], row[1]) if row is not None else (None, None)


def _event_row(write: ControlPlaneAtomicWrite, event: EventEnvelope) -> AgentControlPlaneEventRow:
    linkage = write.command.linkage
    return AgentControlPlaneEventRow(
        event_id=str(event.event_id),
        tenant_id=linkage.tenant_id,
        project_id=linkage.project_id,
        actor_id=linkage.actor_id,
        trace_id=linkage.trace_id,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        sequence=event.sequence,
        event_type=event.event_type,
        schema_version=event.schema_version,
        occurred_at=event.occurred_at,
        producer=event.producer,
        run_id=event.run_id,
        session_id=event.session_id,
        correlation_id=str(event.correlation_id) if event.correlation_id else None,
        causation_id=str(event.causation_id) if event.causation_id else None,
        idempotency_key=event.idempotency_key,
        payload=_plain(event.payload),
    )


def _validate_atomic_write(write: ControlPlaneAtomicWrite) -> None:
    """Reject malformed projections before reserving an idempotency receipt."""
    command = write.command
    if command.target_run_status is None:
        raise ValueError("atomic write must set a target run status")
    if command.approval_id is not None and command.target_approval_status is None:
        raise ValueError("approval atomic write must set a target approval status")
    versions = {(item.aggregate_type, item.aggregate_id): item for item in command.aggregates}
    if ("agent_run", command.run_id) not in versions:
        raise ValueError("atomic write must include the run aggregate version")
    if command.approval_id is not None and ("approval_request", command.approval_id) not in versions:
        raise ValueError("approval atomic write must include its approval aggregate version")
    if len(versions) != len(command.aggregates):
        raise ValueError("aggregate versions must have unique identities")
    if not (
        len(write.events)
        == len(write.outbox_events)
        == len(write.outbox_intents)
        == len(write.transition_idempotency_keys)
    ):
        raise ValueError("events, outbox projections, intents, and transition keys must have equal length")

    identities: set[tuple[str, str]] = set()
    event_ids: set[str] = set()
    for event, outbox_event, intent, transition_key in zip(
        write.events,
        write.outbox_events,
        write.outbox_intents,
        write.transition_idempotency_keys,
        strict=True,
    ):
        identity = (event.aggregate_type, event.aggregate_id)
        version = versions.get(identity)
        if version is None or event.sequence is None or event.sequence != version.next_sequence or identity in identities:
            raise ValueError("event sequence must uniquely match its aggregate cursor target")
        event_id = str(event.event_id)
        if event_id in event_ids:
            raise ValueError("event IDs must be unique")
        identities.add(identity)
        event_ids.add(event_id)
        if event.run_id != command.run_id or event.session_id != command.linkage.session_id:
            raise ValueError("event run/session must match the command scope")
        linkage = event.payload.get("control_plane")
        expected_linkage = {
            "tenant_id": command.linkage.tenant_id,
            "actor_id": command.linkage.actor_id,
            "project_id": command.linkage.project_id,
            "session_id": command.linkage.session_id,
            "trace_id": command.linkage.trace_id,
            "command_idempotency_key": command.command_idempotency_key,
        }
        if not isinstance(linkage, Mapping) or any(
            linkage.get(key) != value for key, value in expected_linkage.items()
        ):
            raise ValueError("event actor/tenant/project/session/trace linkage must match the command")
        aggregate_key = outbox_event.aggregate_event_key(
            tenant_id=command.linkage.tenant_id,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            aggregate_version=event.sequence,
        )
        if (
            outbox_event.event_id != event_id
            or outbox_event.tenant_id != command.linkage.tenant_id
            or outbox_event.aggregate_type != event.aggregate_type
            or outbox_event.aggregate_id != event.aggregate_id
            or outbox_event.aggregate_version != event.sequence
            or outbox_event.event_type != event.event_type
            or _plain(outbox_event.payload) != _plain(event.model_dump(mode="json"))
            or outbox_event.idempotency_key != aggregate_key
            or intent.event_id != event_id
            or intent.deduplication_key != aggregate_key
            or event.idempotency_key != transition_key
        ):
            raise ValueError("outbox event and queue intent must exactly project the domain event")
    if identities != set(versions):
        raise ValueError("every changed aggregate must have exactly one event")
    if command.expected_run_status is None:
        details = command.details
        if details.get("session_id") != command.linkage.session_id:
            raise ValueError("new run details must preserve linked session identity")
        requested_by = details.get("requested_by_employee_id")
        if requested_by is not None and requested_by != command.linkage.actor_id:
            raise ValueError("requested run actor must match authenticated linkage")

    event_by_aggregate = {
        (event.aggregate_type, event.aggregate_id): event for event in write.events
    }
    run_event = event_by_aggregate.get(("agent_run", command.run_id))
    if run_event is None or run_event.payload.get("status") != command.target_run_status.value:
        raise ValueError("run event status must match the command target")
    expected_run_transitions = {
        "agent_run.create": {(None, AgentRunStatus.QUEUED)},
        "agent_run.start": {(AgentRunStatus.QUEUED.value, AgentRunStatus.RUNNING)},
        "agent_run.wait_for_approval": {
            (AgentRunStatus.QUEUED.value, AgentRunStatus.WAITING_APPROVAL),
            (AgentRunStatus.RUNNING.value, AgentRunStatus.WAITING_APPROVAL),
        },
        "agent_run.resume": {(AgentRunStatus.WAITING_APPROVAL.value, AgentRunStatus.RUNNING)},
        "agent_run.succeed": {(AgentRunStatus.RUNNING.value, AgentRunStatus.SUCCEEDED)},
        "agent_run.fail": {
            (AgentRunStatus.QUEUED.value, AgentRunStatus.FAILED),
            (AgentRunStatus.RUNNING.value, AgentRunStatus.FAILED),
        },
        "agent_run.cancel": {
            (AgentRunStatus.QUEUED.value, AgentRunStatus.CANCELLED),
            (AgentRunStatus.RUNNING.value, AgentRunStatus.CANCELLED),
        },
        "agent_run.timeout": {
            (AgentRunStatus.QUEUED.value, AgentRunStatus.FAILED),
            (AgentRunStatus.RUNNING.value, AgentRunStatus.FAILED),
        },
        # A stream frame is an append to the run's ordered event log while the run
        # keeps its status. Without this entry a frame has no expressible write, so
        # the canonical SSE wire could not persist (run_id, sequence) before sending
        # a frame to the transport, and frame identity would stay unprovable.
        "agent_run.stream_frame": {
            (AgentRunStatus.RUNNING.value, AgentRunStatus.RUNNING),
            (AgentRunStatus.WAITING_APPROVAL.value, AgentRunStatus.WAITING_APPROVAL),
        },
        "approval_request.create": {
            (AgentRunStatus.QUEUED.value, AgentRunStatus.WAITING_APPROVAL),
            (AgentRunStatus.RUNNING.value, AgentRunStatus.WAITING_APPROVAL),
        },
        "approval_request.approve": {
            (AgentRunStatus.WAITING_APPROVAL.value, AgentRunStatus.RUNNING),
        },
        "approval_request.deny": {
            (AgentRunStatus.WAITING_APPROVAL.value, AgentRunStatus.FAILED),
        },
        "approval_request.cancel": {
            (AgentRunStatus.WAITING_APPROVAL.value, AgentRunStatus.CANCELLED),
        },
        "approval_request.expire": {
            (AgentRunStatus.WAITING_APPROVAL.value, AgentRunStatus.FAILED),
        },
    }
    expected_run_events = {
        "agent_run.create": "agent_run.created",
        "agent_run.start": "agent_run.started",
        "agent_run.wait_for_approval": "agent_run.waiting_approval",
        "agent_run.resume": "agent_run.resumed",
        "agent_run.succeed": "agent_run.succeeded",
        "agent_run.fail": "agent_run.failed",
        "agent_run.cancel": "agent_run.cancelled",
        "agent_run.timeout": "agent_run.timed_out",
        # Same event name as the operation: a frame is not a lifecycle transition,
        # so it must not reuse a status-bearing event type.
        "agent_run.stream_frame": "agent_run.stream_frame",
        "approval_request.create": "agent_run.waiting_approval",
        "approval_request.approve": "agent_run.resumed",
        "approval_request.deny": "agent_run.failed",
        "approval_request.cancel": "agent_run.cancelled",
        "approval_request.expire": "agent_run.timed_out",
    }
    if run_event.event_type != expected_run_events.get(command.operation):
        raise ValueError("run event type does not match the command operation")
    transition = (
        run_event.payload.get("from_status"),
        command.target_run_status,
    )
    if command.operation not in expected_run_transitions or transition not in expected_run_transitions[command.operation]:
        raise ValueError("run event transition is not allowed for this operation")
    if command.expected_run_status is None:
        if run_event.payload.get("project_id") != command.linkage.project_id:
            raise ValueError("run creation event project must match command scope")
        details = command.details
        if _plain(run_event.payload.get("input_payload")) != _plain(details.get("input_payload") or {}):
            raise ValueError("run creation event input must match persisted command details")
        if run_event.payload.get("requested_by_employee_id") != details.get("requested_by_employee_id"):
            raise ValueError("run creation event actor must match persisted command details")
    elif run_event.payload.get("from_status") != command.expected_run_status.value:
        raise ValueError("run event source status must match the command precondition")
    expected_failure_code = command.details.get("failure_code")
    if command.operation == "approval_request.deny":
        expected_failure_code = "approval.denied"
    elif command.operation == "approval_request.expire":
        expected_failure_code = "approval.expired"
    if run_event.payload.get("failure_code") != expected_failure_code:
        raise ValueError("run event failure code must match command semantics")
    if run_event.payload.get("reason") != command.details.get("reason"):
        raise ValueError("run event reason must match command details")
    if _plain(run_event.payload.get("output_payload")) != _plain(command.details.get("output_payload")):
        raise ValueError("run event output must match command details")
    if command.approval_id is not None and run_event.payload.get("approval_id") != command.approval_id:
        raise ValueError("run event approval identity must match the command")

    expected_approval_transitions = {
        "approval_request.create": (None, ApprovalRequestStatus.PENDING, AgentRunStatus.WAITING_APPROVAL),
        "approval_request.approve": (
            ApprovalRequestStatus.PENDING.value,
            ApprovalRequestStatus.APPROVED,
            AgentRunStatus.RUNNING,
        ),
        "approval_request.deny": (
            ApprovalRequestStatus.PENDING.value,
            ApprovalRequestStatus.DENIED,
            AgentRunStatus.FAILED,
        ),
        "approval_request.cancel": (
            ApprovalRequestStatus.PENDING.value,
            ApprovalRequestStatus.CANCELLED,
            AgentRunStatus.CANCELLED,
        ),
        "approval_request.expire": (
            ApprovalRequestStatus.PENDING.value,
            ApprovalRequestStatus.EXPIRED,
            AgentRunStatus.FAILED,
        ),
    }
    if command.approval_id is None:
        if command.operation.startswith("approval_request.") or any(
            event.aggregate_type == "approval_request" for event in write.events
        ):
            raise ValueError("unlinked approval event is not allowed")
    else:
        expected = expected_approval_transitions.get(command.operation)
        if expected is None or command.target_approval_status is not expected[1] or command.target_run_status is not expected[2]:
            raise ValueError("approval status targets do not match the operation")
        if command.expected_approval_status is not None and command.expected_approval_status.value != expected[0]:
            raise ValueError("approval source status does not match the operation")
        if command.expected_approval_status is None and expected[0] is not None:
            raise ValueError("only approval creation may omit its expected approval status")
        approval_event = event_by_aggregate.get(("approval_request", command.approval_id))
        if approval_event is None or approval_event.payload.get("status") != command.target_approval_status.value:
            raise ValueError("approval event status must match the command target")
        expected_approval_events = {
            "approval_request.create": "approval_request.created",
            "approval_request.approve": "approval_request.approved",
            "approval_request.deny": "approval_request.denied",
            "approval_request.cancel": "approval_request.cancelled",
            "approval_request.expire": "approval_request.expired",
        }
        if approval_event.event_type != expected_approval_events[command.operation]:
            raise ValueError("approval event type does not match the command operation")
        if expected[0] is not None and approval_event.payload.get("from_status") != expected[0]:
            raise ValueError("approval event source status must match the operation")
        if command.operation == "approval_request.create":
            details = command.details
            if (
                approval_event.payload.get("tool_call_id") != details.get("tool_call_id")
                or approval_event.payload.get("approval_kind") != details.get("approval_kind")
                or _plain(approval_event.payload.get("requested_payload"))
                != _plain(details.get("requested_payload"))
                or approval_event.payload.get("expires_at") != details.get("expires_at")
            ):
                raise ValueError("approval creation event must match its persisted details")
        elif (
            approval_event.payload.get("run_id") != command.run_id
            or approval_event.payload.get("reason") != command.details.get("reason")
        ):
            raise ValueError("approval event run and reason must match the command")

    if command.approval_id is not None and not any(
        event.aggregate_type == "approval_request" and event.aggregate_id == command.approval_id
        for event in write.events
    ):
        raise ValueError("approval write must include its approval aggregate event")


def _event_result(event: EventEnvelope, outbox_key: str, queue_key: str):
    from prodavan.application.agent_control_plane.contracts import AtomicEventOutboxResult

    return AtomicEventOutboxResult(
        event_id=str(event.event_id),
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        aggregate_version=event.sequence,
        outbox_idempotency_key=outbox_key,
        queue_deduplication_key=queue_key,
        event_recorded=True,
        queue_intent_recorded=True,
    )


def _target_aggregate_status(write: ControlPlaneAtomicWrite, aggregate_type: str) -> str:
    if aggregate_type == "agent_run":
        return write.command.target_run_status.value
    return write.command.target_approval_status.value


def _run_view(
    row: AgentRunRow,
    *,
    session_id: str | None = None,
    failure_code: str | None = None,
) -> AgentRunView:
    return AgentRunView(
        project_id=row.project_id,
        session_id=session_id if session_id is not None else row.session_id,
        requested_by_employee_id=row.requested_by_employee_id,
        idempotency_key=row.idempotency_key,
        input_payload=row.input_payload or {},
        id=row.id,
        status=AgentRunStatus(row.status),
        attempt=row.attempt,
        output_payload=row.output_payload,
        failure_code=failure_code if failure_code is not None else row.failure_code,
        failure_message=row.failure_message,
        started_at=row.started_at,
        heartbeat_at=row.heartbeat_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _approval_view(
    row: ApprovalRequestRow, *, decided_by_employee_id: str | None = None
) -> ApprovalRequestView:
    return ApprovalRequestView(
        run_id=row.run_id,
        tool_call_id=row.tool_call_id,
        approval_kind=row.approval_kind,
        requested_payload=row.requested_payload or {},
        expires_at=row.expires_at,
        id=row.id,
        status=ApprovalRequestStatus(row.status),
        decision_reason=row.decision_reason,
        decided_by_employee_id=(
            decided_by_employee_id
            if decided_by_employee_id is not None
            else row.decided_by_employee_id
        ),
        requested_at=row.requested_at,
        decided_at=row.decided_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _event_envelope(row: AgentControlPlaneEventRow) -> EventEnvelope:
    return EventEnvelope.model_validate(
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


def _write_digest(write: ControlPlaneAtomicWrite) -> str:
    """Bind immutable write content while treating generated occurrence time as replay metadata."""
    event_rows = []
    for event in write.events:
        body = event.model_dump(mode="json")
        body.pop("occurred_at", None)
        event_rows.append(body)
    outbox_rows = []
    for event in write.outbox_events:
        body = event.model_dump(mode="json")
        body.pop("occurred_at", None)
        payload = dict(body["payload"])
        payload.pop("occurred_at", None)
        body["payload"] = payload
        outbox_rows.append(body)
    body = {
        "command": write.command.command_digest,
        "events": event_rows,
        "outbox_events": outbox_rows,
        "outbox_intents": [intent.model_dump(mode="json") for intent in write.outbox_intents],
        "transition_keys": list(write.transition_idempotency_keys),
    }
    payload = json.dumps(_plain(body), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _scope_key(write: ControlPlaneAtomicWrite) -> str:
    command = write.command
    identity = {
        "tenant": command.linkage.tenant_id,
        "project": command.linkage.project_id,
        "operation": command.operation,
        "run_id": command.run_id,
        "approval_id": command.approval_id,
        "aggregates": sorted(
            (item.aggregate_type, item.aggregate_id) for item in command.aggregates
        ),
        "key": command.command_idempotency_key,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _result_payload(result: ControlPlaneWriteResult) -> dict[str, Any]:
    return {
        "status": result.status.value,
        "reason": result.reason,
        "blocker": result.blocker,
        "run": result.run.model_dump(mode="json") if result.run is not None else None,
        "approval": result.approval.model_dump(mode="json") if result.approval is not None else None,
        "event_results": [item.__dict__ if hasattr(item, "__dict__") else {
            field: getattr(item, field) for field in item.__dataclass_fields__
        } for item in result.event_results],
    }


def _restore_result(payload: dict[str, Any], *, duplicate: bool) -> ControlPlaneWriteResult:
    from dataclasses import replace

    from prodavan.application.agent_control_plane.contracts import AtomicEventOutboxResult

    results = tuple(
        AtomicEventOutboxResult(**{**item, "duplicate": duplicate})
        for item in payload["event_results"]
    )
    status = ControlPlaneWriteStatus(payload["status"])
    if duplicate and status is ControlPlaneWriteStatus.APPLIED:
        status = ControlPlaneWriteStatus.DUPLICATE
    return ControlPlaneWriteResult(
        status=status,
        event_results=results,
        run=AgentRunView.model_validate(payload["run"]) if payload.get("run") else None,
        approval=ApprovalRequestView.model_validate(payload["approval"]) if payload.get("approval") else None,
        reason=payload.get("reason"),
        blocker=payload.get("blocker"),
    )


def _is_unique_violation(error: IntegrityError) -> bool:
    """Recognize duplicate-key races without masking FK/check/data failures."""
    original = error.orig
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if sqlstate is not None:
        return sqlstate == "23505"
    message = str(original).lower()
    return "unique constraint failed" in message or "primary key constraint failed" in message


def _blocked(blocker: str) -> ControlPlaneWriteResult:
    return ControlPlaneWriteResult(
        status=ControlPlaneWriteStatus.BLOCKED,
        blocker=blocker,
        reason="tenant, project, session or authenticated actor is outside the persisted scope",
    )


def _conflict(reason: str) -> ControlPlaneWriteResult:
    return ControlPlaneWriteResult(status=ControlPlaneWriteStatus.CONFLICT, reason=reason)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def _event_time(events: tuple[EventEnvelope, ...]) -> datetime:
    return max(event.occurred_at for event in events)


__all__ = ["SqlAlchemyAgentControlPlaneRepository"]
