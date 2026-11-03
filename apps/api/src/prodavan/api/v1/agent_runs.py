"""Employee API for durable Agent Runs and Approval decisions."""

from __future__ import annotations

from hashlib import sha256
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from prodavan.api.deps import PrincipalDep, SessionDep, get_current_employee
from prodavan.api.migration_agent_runs.dependencies import (
    AgentRunBackend,
    AgentRunBackendDep,
    FEATURE_FLAG,
)
from prodavan.api.migration_agent_runs.schemas import (
    AgentRunCancelled,
    AgentRunCreated,
    AgentRunDispatchReport,
    AgentRunList,
    AgentRunRecord,
    ApprovalDecisionBody,
    ApprovalDecisionResult,
    ApprovalInbox,
    ApprovalInboxItem,
    ApprovalRecord,
    CancelAgentRunBody,
    CreateAgentRunBody,
    EventTimeline,
    EventTimelineQuery,
)
from prodavan.application.agent_runs import (
    AgentRunPersistenceBlocked,
    AgentRunsQueryService,
    ApprovalPageItem,
    encode_cursor,
)
from prodavan.application.agent_runs.inbox import list_pending_approvals
from prodavan.application.agent_control_plane import (
    ControlPlaneWriteError,
    ControlPlaneWriteStatus,
)
from prodavan.application.agent_control_plane.contracts import ControlPlaneLinkage
from prodavan.application.project_service.access import ProjectAccessPolicy
from prodavan.domain.agent.control_plane import (
    AgentRunCreate,
    AgentRunStatus,
    ApprovalDecision,
    ApprovalRequestStatus,
)
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

router = APIRouter(tags=["agent-runs"])
EmployeeDep = Annotated[EmployeeRow | None, Depends(get_current_employee)]
_IDEMPOTENCY_HEADER = "Idempotency-Key"


class CancelApprovalBody(BaseModel):
    model_config = {"extra": "forbid"}

    expected_approval_sequence: int = Field(ge=1)
    expected_run_sequence: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=2000)


def _require_employee(employee: EmployeeRow | None) -> EmployeeRow:
    if employee is None:
        raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="employee required")
    return employee


def _tenant_ids(employee: EmployeeRow) -> frozenset[str]:
    memberships = getattr(employee, "memberships", None) or ()
    return frozenset(
        str(membership.company_id)
        for membership in memberships
        if getattr(membership, "company_id", None)
    )


def _project_tenant_id(*, employee: EmployeeRow, project_company_id: str) -> str:
    if project_company_id not in _tenant_ids(employee):
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Project not found")
    return project_company_id


def _command_trace_id(*, tenant_id: str, actor_id: str, project_id: str, key: str) -> str:
    """Keep durable command provenance stable when an HTTP request is replayed."""
    digest = sha256(f"{tenant_id}\0{actor_id}\0{project_id}\0{key}".encode("utf-8")).hexdigest()
    return f"agent-command:{digest}"


def _idempotency_key(value: str | None) -> str:
    key = (value or "").strip()
    if not key or len(key) > 128:
        raise AppError(
            code="IDEMPOTENCY_KEY_REQUIRED",
            title="Idempotency key required",
            status=400,
            detail=f"{_IDEMPOTENCY_HEADER} header must contain 1 to 128 characters",
        )
    return key


def _linkage(
    *,
    tenant_id: str,
    employee: EmployeeRow,
    project_id: str,
    session_id: str | None,
    idempotency_key: str,
):
    return ControlPlaneLinkage(
        tenant_id=tenant_id,
        actor_id=str(employee.id),
        project_id=project_id,
        session_id=session_id,
        trace_id=_command_trace_id(
            tenant_id=tenant_id,
            actor_id=str(employee.id),
            project_id=project_id,
            key=idempotency_key,
        ),
    )


def _record(run: Any, *, sequence: int | None = None) -> AgentRunRecord:
    data = run.model_dump(mode="python") if hasattr(run, "model_dump") else dict(run)
    data["sequence"] = max(1, sequence or 1)
    return AgentRunRecord.model_validate(data)


def _approval_record(approval: Any) -> ApprovalRecord:
    data = approval.model_dump(mode="python") if hasattr(approval, "model_dump") else dict(approval)
    return ApprovalRecord.model_validate(data)


def _write_error(result) -> AppError:
    if result.status is ControlPlaneWriteStatus.CONFLICT:
        return AppError(
            code="AGENT_RUN_CONFLICT",
            title="Agent Run conflict",
            status=409,
            detail=result.reason or "Agent Run state changed; reload and retry with a new command key",
        )
    if result.status is ControlPlaneWriteStatus.BLOCKED:
        return AppError(
            code="AGENT_RUN_PERSISTENCE_BLOCKED",
            title="Agent Run persistence blocked",
            status=503,
            detail=result.reason or "durable Agent Run persistence is blocked",
            extra={"blocker": result.blocker, "feature_flag": FEATURE_FLAG},
        )
    return AppError(
        code="AGENT_RUN_WRITE_FAILED",
        title="Agent Run write failed",
        status=503,
        detail="durable Agent Run command did not commit",
    )


def _queue_count(result) -> int:
    # These rows prove a durable enqueue intent only; they do not prove broker
    # delivery. The response property is deliberately named queue_intents.
    return len(result.event_results)


async def _dispatch_committed(
    *,
    backend: AgentRunBackend,
    trace_id: str,
    result,
) -> AgentRunDispatchReport:
    """Hand committed enqueue intents to the durable consumer.

    Called only after the control-plane transaction returned ``applied`` or
    ``duplicate``: the state transition, its events, the outbox projection and
    the enqueue intents were written in that one transaction. This call never
    writes domain state; it asks the consumer to claim the intents it just made
    durable. Failure is reported, not raised — the pending row remains the
    durable handoff for the next consumer round.
    """
    return await backend.dispatch_after_commit(
        trace_id=trace_id,
        intent_count=_queue_count(result),
    )


async def _project(
    *,
    project_id: str,
    principal: Principal,
    session,
    employee: EmployeeRow | None,
    write: bool,
):
    current_employee = _require_employee(employee)
    project = await ProjectAccessPolicy(session).require_access(
        project_id=project_id,
        principal=principal,
        employee=current_employee,
        write=write,
        allow_paused=not write,
    )
    return current_employee, _project_tenant_id(
        employee=current_employee,
        project_company_id=str(project.company_id),
    ), project


@router.get("/projects/{project_id}/agent/runs", response_model=AgentRunList)
async def list_agent_runs(
    project_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    backend: AgentRunBackendDep,
    response: Response,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
) -> AgentRunList:
    _, tenant_id, _ = await _project(
        project_id=project_id, principal=principal, session=session, employee=employee, write=False
    )
    if cursor is not None and offset:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="cursor and offset cannot be combined",
        )
    try:
        page = await AgentRunsQueryService(session).list_runs(
            tenant_id=tenant_id,
            project_id=project_id,
            limit=limit,
            cursor=cursor,
            offset=offset,
        )
    except ValueError as exc:
        raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail=str(exc)) from exc
    except AgentRunPersistenceBlocked as exc:
        raise AppError(
            code="AGENT_RUN_PERSISTENCE_BLOCKED",
            title="Agent Run unavailable",
            status=503,
            detail=str(exc),
            extra={"blocker": exc.blocker, "feature_flag": FEATURE_FLAG},
        ) from exc
    response.headers["X-Has-More"] = str(page.has_more).lower()
    if page.next_cursor is not None:
        response.headers["X-Next-Cursor"] = page.next_cursor
    items = [
        _record(
            _run_view_from_row(item.run, session_id=item.session_id),
            sequence=item.sequence,
        )
        for item in page.items
    ]
    return AgentRunList(items=items, limit=limit, offset=offset)


def _run_view_from_row(row, *, session_id: str | None):
    from prodavan.domain.agent.control_plane import AgentRunView

    return AgentRunView(
        id=row.id,
        project_id=row.project_id,
        session_id=session_id,
        requested_by_employee_id=row.requested_by_employee_id,
        idempotency_key=row.idempotency_key,
        status=AgentRunStatus(row.status),
        attempt=row.attempt,
        input_payload=row.input_payload or {},
        output_payload=row.output_payload,
        failure_code=row.failure_code,
        failure_message=row.failure_message,
        started_at=row.started_at,
        heartbeat_at=row.heartbeat_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("/projects/{project_id}/agent/runs", status_code=201, response_model=AgentRunCreated)
async def create_agent_run(
    project_id: str,
    body: CreateAgentRunBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    backend: AgentRunBackendDep,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> AgentRunCreated:
    actor, tenant_id, _ = await _project(
        project_id=project_id, principal=principal, session=session, employee=employee, write=True
    )
    backend.require_writes()
    key = _idempotency_key(idempotency_key)
    linkage = _linkage(
        tenant_id=tenant_id,
        employee=actor,
        project_id=project_id,
        session_id=body.session_id,
        idempotency_key=key,
    )
    command = AgentRunCreate(
        project_id=project_id,
        session_id=body.session_id,
        requested_by_employee_id=str(actor.id),
        idempotency_key=key,
        input_payload=body.input_payload,
    )
    try:
        result = await backend.control_plane(tenant_id=tenant_id).submit_run(command, linkage=linkage)
    except ControlPlaneWriteError as exc:
        raise AppError(
            code="AGENT_RUN_PERSISTENCE_FAILED",
            title="Agent Run persistence failed",
            status=503,
            detail="durable Agent Run command could not commit",
        ) from exc
    if result.status not in {ControlPlaneWriteStatus.APPLIED, ControlPlaneWriteStatus.DUPLICATE}:
        raise _write_error(result)
    assert result.run is not None
    dispatch = await _dispatch_committed(
        backend=backend,
        trace_id=linkage.trace_id,
        result=result,
    )
    return AgentRunCreated(
        run=_record(result.run, sequence=result.event_results[0].aggregate_version),
        duplicate=result.status is ControlPlaneWriteStatus.DUPLICATE,
        queue_intents_recorded=_queue_count(result),
        dispatch=dispatch,
    )


@router.get("/projects/{project_id}/agent/runs/{run_id}", response_model=AgentRunRecord)
async def get_agent_run(
    project_id: str,
    run_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    backend: AgentRunBackendDep,
) -> AgentRunRecord:
    _, tenant_id, _ = await _project(
        project_id=project_id, principal=principal, session=session, employee=employee, write=False
    )
    try:
        item = await AgentRunsQueryService(session).get_run(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=run_id,
        )
    except AgentRunPersistenceBlocked as exc:
        raise AppError(
            code="AGENT_RUN_PERSISTENCE_BLOCKED",
            title="Agent Run unavailable",
            status=503,
            detail=str(exc),
            extra={"blocker": exc.blocker, "feature_flag": FEATURE_FLAG},
        ) from exc
    if item is None:
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent run not found")
    return _record(
        _run_view_from_row(item.run, session_id=item.session_id),
        sequence=item.sequence,
    )


@router.post("/projects/{project_id}/agent/runs/{run_id}/cancel", response_model=AgentRunCancelled)
async def cancel_agent_run(
    project_id: str,
    run_id: str,
    body: CancelAgentRunBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    backend: AgentRunBackendDep,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> AgentRunCancelled:
    actor, tenant_id, _ = await _project(
        project_id=project_id, principal=principal, session=session, employee=employee, write=True
    )
    key = _idempotency_key(idempotency_key)
    backend.require_writes()
    run = await backend.transaction.get_run(tenant_id=tenant_id, run_id=run_id)
    if run is None or run.project_id != project_id:
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent run not found")
    linkage = _linkage(
        tenant_id=tenant_id,
        employee=actor,
        project_id=project_id,
        session_id=run.session_id,
        idempotency_key=key,
    )
    try:
        result = await backend.control_plane(tenant_id=tenant_id).cancel_run(
            run_id=run_id,
            linkage=linkage,
            expected_sequence=body.expected_sequence,
            expected_status=body.expected_status,
            command_idempotency_key=key,
            reason=body.reason,
        )
    except (ControlPlaneWriteError, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise AppError(code="AGENT_RUN_CONFLICT", title="Agent Run conflict", status=409,
                           detail="run is not cancellable in its current state") from exc
        raise AppError(code="AGENT_RUN_PERSISTENCE_FAILED", title="Agent Run persistence failed", status=503,
                       detail="durable Agent Run command could not commit") from exc
    if result.status not in {ControlPlaneWriteStatus.APPLIED, ControlPlaneWriteStatus.DUPLICATE}:
        raise _write_error(result)
    assert result.run is not None
    dispatch = await _dispatch_committed(
        backend=backend,
        trace_id=linkage.trace_id,
        result=result,
    )
    return AgentRunCancelled(
        run=_record(
            result.run,
            sequence=result.event_results[0].aggregate_version,
        ),
        duplicate=result.status is ControlPlaneWriteStatus.DUPLICATE,
        queue_intents_recorded=_queue_count(result),
        dispatch=dispatch,
    )


@router.get("/agent/approvals/inbox", response_model=ApprovalInbox)
async def approval_inbox(
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    backend: AgentRunBackendDep,
    response: Response,
    project_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=2048),
) -> ApprovalInbox:
    actor = _require_employee(employee)
    tenant_ids = _tenant_ids(actor)
    if not tenant_ids:
        raise AppError(
            code="AGENT_RUN_TENANT_REQUIRED",
            title="Employee tenant required",
            status=403,
            detail="employee has no active tenant membership",
        )
    if project_id is not None:
        await _project(
            project_id=project_id,
            principal=principal,
            session=session,
            employee=actor,
            write=False,
        )
    from prodavan.application.agent_runs.inbox import list_pending_approvals

    scan_cursor = cursor
    visible: list[ApprovalPageItem] = []
    next_cursor = None
    exhausted = False
    while len(visible) < limit + 1 and not exhausted:
        try:
            page = await list_pending_approvals(
                session=session,
                tenant_ids=tenant_ids,
                project_id=project_id,
                limit=min(200, max(limit + 1, 32)),
                cursor=scan_cursor,
            )
        except ValueError as exc:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail=str(exc)) from exc
        except AgentRunPersistenceBlocked as exc:
            raise AppError(
                code="AGENT_RUN_PERSISTENCE_BLOCKED",
                title="Agent Run unavailable",
                status=503,
                detail=str(exc),
                extra={"blocker": exc.blocker, "feature_flag": FEATURE_FLAG},
            ) from exc
        for item in page.items:
            try:
                project = await ProjectAccessPolicy(session).require_access(
                    project_id=item.scope.project_id,
                    principal=principal,
                    employee=actor,
                    write=False,
                    allow_paused=True,
                )
            except AppError as exc:
                if exc.status in {403, 404}:
                    continue
                raise
            if str(project.company_id) not in tenant_ids:
                continue
            visible.append(item)
            if len(visible) == limit + 1:
                break
        exhausted = not page.has_more
        scan_cursor = page.next_cursor
    has_more = len(visible) > limit
    visible_page = visible[:limit]
    next_cursor = None
    if has_more and visible_page:
        item = visible_page[-1]
        next_cursor = encode_cursor(
            {
                "kind": "approval",
                "tenants": sorted(tenant_ids),
                "project": project_id,
                "requested_at": item.approval.requested_at.isoformat(),
                "approval_id": item.approval.id,
            }
        )
    response.headers["X-Has-More"] = str(has_more).lower()
    if next_cursor is not None:
        response.headers["X-Next-Cursor"] = next_cursor
    items = [
        ApprovalInboxItem(
            approval=_approval_record(_approval_view_from_row(item.approval)),
            project_id=item.scope.project_id,
            session_id=item.scope.session_id,
            run_status=AgentRunStatus(item.run.status),
            approval_sequence=item.approval_sequence,
            run_sequence=item.run_sequence,
        )
        for item in visible_page
    ]
    return ApprovalInbox(items=items, limit=limit)


def _approval_view_from_row(row):
    from prodavan.domain.agent.control_plane import ApprovalRequestView

    return ApprovalRequestView(
        id=row.id,
        run_id=row.run_id,
        tool_call_id=row.tool_call_id,
        approval_kind=row.approval_kind,
        status=ApprovalRequestStatus(row.status),
        requested_payload=row.requested_payload or {},
        decision_reason=row.decision_reason,
        decided_by_employee_id=row.decided_by_employee_id,
        requested_at=row.requested_at,
        expires_at=row.expires_at,
        decided_at=row.decided_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("/projects/{project_id}/agent/approvals/{approval_id}/decision", response_model=ApprovalDecisionResult)
async def decide_agent_approval(
    project_id: str,
    approval_id: str,
    body: ApprovalDecisionBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    backend: AgentRunBackendDep,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> ApprovalDecisionResult:
    actor, tenant_id, _ = await _project(
        project_id=project_id, principal=principal, session=session, employee=employee, write=True
    )
    key = _idempotency_key(idempotency_key)
    backend.require_writes()
    from prodavan.infrastructure.persistence.agent_control_plane.models import (
        AgentControlPlaneAggregateRow,
        AgentControlPlaneRunScopeRow,
    )
    from prodavan.infrastructure.persistence.models.agent_control_plane import (
        AgentRunRow,
        ApprovalRequestRow,
    )

    row = await session.execute(
        select(ApprovalRequestRow, AgentRunRow, AgentControlPlaneRunScopeRow)
        .join(AgentRunRow, AgentRunRow.id == ApprovalRequestRow.run_id)
        .join(AgentControlPlaneRunScopeRow, AgentControlPlaneRunScopeRow.run_id == AgentRunRow.id)
        .where(
            ApprovalRequestRow.id == approval_id,
            AgentControlPlaneRunScopeRow.tenant_id == tenant_id,
            AgentControlPlaneRunScopeRow.project_id == project_id,
        )
    )
    found = row.one_or_none()
    if found is None:
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Approval request not found")
    approval, run, scope = found
    await ProjectAccessPolicy(session).require_access(
        project_id=scope.project_id, principal=principal, employee=actor, write=True, allow_paused=True
    )
    approval_cursor = await session.get(AgentControlPlaneAggregateRow, ("approval_request", approval_id))
    run_cursor = await session.get(AgentControlPlaneAggregateRow, ("agent_run", run.id))
    if approval_cursor is None or run_cursor is None:
        raise AppError(
            code="AGENT_RUN_PERSISTENCE_BLOCKED",
            title="Agent Run unavailable",
            status=503,
            detail="approval aggregate cursor is missing",
            extra={"blocker": "agent_control_plane_aggregate_cursor_missing"},
        )
    status = ApprovalRequestStatus.APPROVED if body.decision == "approve" else ApprovalRequestStatus.DENIED
    linkage = _linkage(
        tenant_id=tenant_id,
        employee=actor,
        project_id=project_id,
        session_id=scope.session_id,
        idempotency_key=key,
    )
    try:
        result = await backend.control_plane(tenant_id=tenant_id).decide_approval(
            approval_id,
            ApprovalDecision(
                status=status,
                decided_by_employee_id=str(actor.id),
                decision_reason=body.reason,
            ),
            run_id=run.id,
            linkage=linkage,
            expected_approval_sequence=body.expected_approval_sequence,
            expected_run_sequence=body.expected_run_sequence,
            command_idempotency_key=key,
        )
    except (ControlPlaneWriteError, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise AppError(code="AGENT_RUN_CONFLICT", title="Approval conflict", status=409,
                           detail="approval transition rejected") from exc
        raise AppError(code="AGENT_RUN_PERSISTENCE_FAILED", title="Agent Run persistence failed", status=503,
                       detail="durable approval command could not commit") from exc
    if result.status not in {ControlPlaneWriteStatus.APPLIED, ControlPlaneWriteStatus.DUPLICATE}:
        raise _write_error(result)
    assert result.run is not None and result.approval is not None
    dispatch = await _dispatch_committed(
        backend=backend,
        trace_id=linkage.trace_id,
        result=result,
    )
    return ApprovalDecisionResult(
        approval=_approval_record(result.approval),
        run=_record(
            result.run,
            sequence=next(
                event.aggregate_version
                for event in result.event_results
                if event.aggregate_type == "agent_run"
            ),
        ),
        duplicate=result.status is ControlPlaneWriteStatus.DUPLICATE,
        queue_intents_recorded=_queue_count(result),
        dispatch=dispatch,
    )


@router.post(
    "/projects/{project_id}/agent/approvals/{approval_id}/approve",
    response_model=ApprovalDecisionResult,
)
async def approve_agent_approval(
    project_id: str,
    approval_id: str,
    body: ApprovalDecisionBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    backend: AgentRunBackendDep,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> ApprovalDecisionResult:
    if body.decision != "approve":
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="approve endpoint requires decision=approve",
        )
    return await decide_agent_approval(
        project_id=project_id,
        approval_id=approval_id,
        body=body,
        principal=principal,
        session=session,
        employee=employee,
        backend=backend,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/projects/{project_id}/agent/approvals/{approval_id}/reject",
    response_model=ApprovalDecisionResult,
)
async def reject_agent_approval(
    project_id: str,
    approval_id: str,
    body: ApprovalDecisionBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    backend: AgentRunBackendDep,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> ApprovalDecisionResult:
    if body.decision != "deny":
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="reject endpoint requires decision=deny",
        )
    return await decide_agent_approval(
        project_id=project_id,
        approval_id=approval_id,
        body=body,
        principal=principal,
        session=session,
        employee=employee,
        backend=backend,
        idempotency_key=idempotency_key,
    )


@router.get("/projects/{project_id}/agent/runs/{run_id}/events", response_model=EventTimeline)
async def agent_run_event_timeline(
    project_id: str,
    run_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    backend: AgentRunBackendDep,
    response: Response,
    limit: int = Query(default=100, ge=1, le=500),
    aggregate_type: str | None = Query(default=None, pattern="^(agent_run|approval_request)$"),
    aggregate_id: str | None = Query(default=None, max_length=64),
    after_sequence: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=2048),
) -> EventTimeline:
    actor, tenant_id, _ = await _project(
        project_id=project_id, principal=principal, session=session, employee=employee, write=False
    )
    _ = actor
    query_cursor = EventTimelineQuery(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        after_sequence=after_sequence,
    )
    try:
        query_cursor.validate_cursor()
    except ValueError as exc:
        raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail=str(exc)) from exc
    run = await backend.transaction.get_run(tenant_id=tenant_id, run_id=run_id)
    if run is None or run.project_id != project_id:
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Agent run not found")
    try:
        page = await AgentRunsQueryService(session).list_events(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=run_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            after_sequence=after_sequence,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail=str(exc)) from exc
    except AgentRunPersistenceBlocked as exc:
        raise AppError(
            code="AGENT_RUN_PERSISTENCE_BLOCKED",
            title="Agent Run unavailable",
            status=503,
            detail=str(exc),
            extra={"blocker": exc.blocker, "feature_flag": FEATURE_FLAG},
        ) from exc
    response.headers["X-Has-More"] = str(page.has_more).lower()
    if page.next_cursor is not None:
        response.headers["X-Next-Cursor"] = page.next_cursor
    return EventTimeline(run_id=run_id, items=list(page.items), has_more=page.has_more, limit=limit)


@router.post(
    "/projects/{project_id}/agent/approvals/{approval_id}/cancel",
    response_model=ApprovalDecisionResult,
)
async def cancel_agent_approval(
    project_id: str,
    approval_id: str,
    body: CancelApprovalBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    backend: AgentRunBackendDep,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> ApprovalDecisionResult:
    actor, tenant_id, _ = await _project(
        project_id=project_id, principal=principal, session=session, employee=employee, write=True
    )
    key = _idempotency_key(idempotency_key)
    backend.require_writes()
    from prodavan.infrastructure.persistence.agent_control_plane.models import (
        AgentControlPlaneAggregateRow,
        AgentControlPlaneRunScopeRow,
    )
    from prodavan.infrastructure.persistence.models.agent_control_plane import AgentRunRow, ApprovalRequestRow

    found = (
        await session.execute(
            select(ApprovalRequestRow, AgentRunRow, AgentControlPlaneRunScopeRow)
            .join(AgentRunRow, AgentRunRow.id == ApprovalRequestRow.run_id)
            .join(AgentControlPlaneRunScopeRow, AgentControlPlaneRunScopeRow.run_id == AgentRunRow.id)
            .where(
                ApprovalRequestRow.id == approval_id,
                AgentControlPlaneRunScopeRow.tenant_id == tenant_id,
                AgentControlPlaneRunScopeRow.project_id == project_id,
            )
        )
    ).one_or_none()
    if found is None:
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Approval request not found")
    approval, run, scope = found
    await ProjectAccessPolicy(session).require_access(
        project_id=project_id, principal=principal, employee=actor, write=True, allow_paused=True
    )
    approval_cursor = await session.get(AgentControlPlaneAggregateRow, ("approval_request", approval_id))
    run_cursor = await session.get(AgentControlPlaneAggregateRow, ("agent_run", run.id))
    if approval_cursor is None or run_cursor is None:
        raise AppError(
            code="AGENT_RUN_PERSISTENCE_BLOCKED",
            title="Agent Run unavailable",
            status=503,
            detail="approval or run aggregate cursor is missing",
            extra={"blocker": "agent_control_plane_aggregate_cursor_missing", "feature_flag": FEATURE_FLAG},
        )
    linkage = _linkage(
        tenant_id=tenant_id,
        employee=actor,
        project_id=project_id,
        session_id=scope.session_id,
        idempotency_key=key,
    )
    try:
        result = await backend.control_plane(tenant_id=tenant_id).cancel_approval(
            approval_id=approval_id,
            run_id=run.id,
            linkage=linkage,
            expected_approval_sequence=body.expected_approval_sequence,
            expected_run_sequence=body.expected_run_sequence,
            command_idempotency_key=key,
            reason=body.reason,
        )
    except (ControlPlaneWriteError, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise AppError(
                code="AGENT_RUN_CONFLICT", title="Approval conflict", status=409,
                detail="approval transition rejected",
            ) from exc
        raise AppError(
            code="AGENT_RUN_PERSISTENCE_FAILED", title="Agent Run persistence failed", status=503,
            detail="durable approval cancellation could not commit",
        ) from exc
    if result.status not in {ControlPlaneWriteStatus.APPLIED, ControlPlaneWriteStatus.DUPLICATE}:
        raise _write_error(result)
    assert result.run is not None and result.approval is not None
    dispatch = await _dispatch_committed(
        backend=backend,
        trace_id=linkage.trace_id,
        result=result,
    )
    return ApprovalDecisionResult(
        approval=_approval_record(result.approval),
        run=_record(
            result.run,
            sequence=next(
                event.aggregate_version
                for event in result.event_results
                if event.aggregate_type == "agent_run"
            ),
        ),
        duplicate=result.status is ControlPlaneWriteStatus.DUPLICATE,
        queue_intents_recorded=_queue_count(result),
        dispatch=dispatch,
    )


__all__ = ["router"]
