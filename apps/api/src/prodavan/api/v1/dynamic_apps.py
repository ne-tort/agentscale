"""Dynamic declarative apps — definition/version/instance/grant CRUD (v1).

Scope and trust model
---------------------
The ``{company_id}`` path segment is an **assertion**, not the authority. It is resolved
through :class:`EntitlementService` and every write then uses ``CompanyRow.id`` from that
resolution as ``tenant_id``; a caller can never place a row in a tenant it does not own,
and a foreign tenant is answered with ``404`` exactly like a missing one.

Every mutating request requires ``Idempotency-Key``. An exact repeat returns the original
receipt (``replayed`` in ``meta``); the same key with a different body is ``409``
``IDEMPOTENCY_REPLAY``. All row changes and the audit record commit in one transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, Field

from prodavan.api.deps import PrincipalDep, SessionDep, get_current_employee
from prodavan.application.dynamic_apps_definition import (
    ActorIdentity,
    ArchiveDefinitionRequest,
    CreateDefinitionRequest,
    CreateVersionRequest,
    DynamicAppCrudService,
    DynamicAppErrorCode,
    DynamicAppOperation,
    GrantRequest,
    InstallInstanceRequest,
    IssueGrantRequest,
    MutationContext,
    PublishVersionRequest,
    RevokeGrantRequest,
)
from prodavan.application.dynamic_apps_definition.sqlalchemy_adapter import (
    PlatformEventAuditAdapter,
    SqlAlchemyDynamicAppRepository,
    SqlAlchemyScopeAuthority,
)
from prodavan.application.dynamic_apps_definition.lifecycle import (
    archived_persisted_status,
)
from prodavan.domain.dynamic_apps import (
    AppLifecycleStatus,
    AppScopeKind,
    CapabilityDecision,
)
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

router = APIRouter(
    prefix="/companies/{company_id}/dynamic-apps", tags=["dynamic-apps"]
)

_IDEMPOTENCY_HEADER = "Idempotency-Key"
EmployeeDep = Annotated[EmployeeRow | None, Depends(get_current_employee)]


# ----------------------------------------------------------------------------- bodies


class CreateDefinitionBody(BaseModel):
    model_config = {"extra": "forbid"}

    app_id: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9-]{2,63}$")
    display_name: str = Field(min_length=1, max_length=260)
    description: str | None = Field(default=None, max_length=2000)


class CreateVersionBody(BaseModel):
    model_config = {"extra": "forbid"}

    manifest: dict[str, Any]
    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    artifact_uri: str | None = Field(default=None, max_length=2048)


class PublishVersionBody(BaseModel):
    model_config = {"extra": "forbid"}

    expected_current_version_id: str | None = Field(default=None, max_length=80)


class ArchiveDefinitionBody(BaseModel):
    model_config = {"extra": "forbid"}

    reason: str | None = Field(default=None, max_length=2000)


class InstallInstanceBody(BaseModel):
    model_config = {"extra": "forbid"}

    scope_kind: str = Field(pattern=r"^(cabinet|project)$")
    scope_id: str = Field(min_length=1, max_length=80)
    version_id: str | None = Field(default=None, max_length=80)
    runtime_ref: str | None = Field(default=None, max_length=200)


class GrantCapabilityBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=120)
    decision: str = Field(default="granted", pattern=r"^(granted|denied)$")
    constraints: dict[str, Any] | None = None
    expires_at: datetime | None = None


class IssueGrantBody(BaseModel):
    model_config = {"extra": "forbid"}

    scope_kind: str = Field(pattern=r"^(cabinet|project)$")
    scope_id: str = Field(min_length=1, max_length=80)
    capabilities: list[GrantCapabilityBody] = Field(min_length=1, max_length=64)
    expires_at: datetime | None = None


class RevokeGrantBody(BaseModel):
    model_config = {"extra": "forbid"}

    scope_kind: str = Field(pattern=r"^(cabinet|project)$")
    scope_id: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=2000)


# --------------------------------------------------------------------------- helpers


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Server-resolved tenant/actor pair; the client's path segment is only asserted."""

    tenant_id: str
    actor_id: str


async def resolve_tenant_context(
    *,
    company_id: str,
    principal: Principal,
    session,
    employee: EmployeeRow | None,
) -> TenantContext:
    from prodavan.application.identity.service import EntitlementService

    company = await EntitlementService(session).require_company_actor(
        principal, company_id, employee=employee
    )
    actor_id = employee.id if employee is not None else principal.sub
    # ``company.id`` — never the raw path value — becomes the write tenant.
    return TenantContext(tenant_id=company.id, actor_id=actor_id)


def _idempotency_key(value: str | None) -> str:
    key = (value or "").strip()
    if not key or len(key) > 128:
        raise AppError(
            code=DynamicAppErrorCode.IDEMPOTENCY_KEY_REQUIRED.value,
            title="Idempotency key required",
            status=400,
            detail=f"{_IDEMPOTENCY_HEADER} header must contain 1 to 128 characters",
        )
    return key


def _service(session) -> DynamicAppCrudService:
    return DynamicAppCrudService(
        SqlAlchemyDynamicAppRepository(session),
        PlatformEventAuditAdapter(session),
        scope_authority=SqlAlchemyScopeAuthority(session),
    )


def _context(
    *, tenant: TenantContext, operation: DynamicAppOperation, idempotency_key: str | None
) -> MutationContext:
    return MutationContext(
        identity=ActorIdentity(tenant_id=tenant.tenant_id, actor_id=tenant.actor_id),
        idempotency_key=_idempotency_key(idempotency_key),
        operation=operation,
    )


def _envelope(
    data: Any,
    *,
    trace_id: str | None = None,
    replayed: bool | None = None,
    count: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if trace_id is not None:
        meta["trace_id"] = trace_id
    if replayed is not None:
        meta["replayed"] = replayed
    if count is not None:
        meta["count"] = count
    payload: dict[str, Any] = {"data": data, "meta": meta}
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def _scope_kind(value: str) -> AppScopeKind:
    try:
        return AppScopeKind(value)
    except ValueError:  # pragma: no cover - regex already restricts the value
        raise AppError(
            code=DynamicAppErrorCode.INVALID_SCOPE.value,
            title="Invalid scope",
            status=422,
            detail="scope_kind must be cabinet or project",
        ) from None


async def _definition_id_for(*, service: DynamicAppCrudService, tenant_id: str, app_id: str) -> str:
    definition = await service.get_definition(identity_tenant_id=tenant_id, app_id=app_id)
    return definition["id"]


# ----------------------------------------------------------------------- definitions


@router.get("")
async def list_dynamic_apps(
    company_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    lifecycle: str | None = Query(default=None, pattern=r"^(draft|published|archived)$"),
) -> dict:
    tenant = await resolve_tenant_context(
        company_id=company_id, principal=principal, session=session, employee=employee
    )
    persisted = _lifecycle_filter(lifecycle)
    page = await _service(session).list_definitions(
        identity_tenant_id=tenant.tenant_id,
        limit=limit,
        after_id=cursor,
        lifecycle_status=persisted,
    )
    return _envelope(
        page["items"],
        count=len(page["items"]),
        next_cursor=page["next_cursor"],
        has_more=page["has_more"],
    )


@router.post("", status_code=201)
async def create_dynamic_app(
    company_id: str,
    body: CreateDefinitionBody,
    response: Response,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> dict:
    tenant = await resolve_tenant_context(
        company_id=company_id, principal=principal, session=session, employee=employee
    )
    context = _context(
        tenant=tenant,
        operation=DynamicAppOperation.CREATE_DEFINITION,
        idempotency_key=idempotency_key,
    )
    receipt = await _service(session).create_definition(
        context=context,
        request=CreateDefinitionRequest(
            app_id=body.app_id,
            display_name=body.display_name,
            description=body.description,
        ),
    )
    if receipt.replayed:
        response.status_code = 200
    return _envelope(receipt.data, trace_id=receipt.trace_id, replayed=receipt.replayed)


@router.get("/{app_id}")
async def get_dynamic_app(
    company_id: str,
    app_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    tenant = await resolve_tenant_context(
        company_id=company_id, principal=principal, session=session, employee=employee
    )
    data = await _service(session).get_definition(
        identity_tenant_id=tenant.tenant_id, app_id=app_id
    )
    return _envelope(data)


@router.post("/{app_id}/archive")
async def archive_dynamic_app(
    company_id: str,
    app_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    body: ArchiveDefinitionBody | None = None,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> dict:
    tenant = await resolve_tenant_context(
        company_id=company_id, principal=principal, session=session, employee=employee
    )
    # Validate the idempotency header before any read so a malformed request is a 400
    # regardless of whether the target exists.
    idempotency_key = _idempotency_key(idempotency_key)
    service = _service(session)
    definition_id = await _definition_id_for(
        service=service, tenant_id=tenant.tenant_id, app_id=app_id
    )
    payload = body or ArchiveDefinitionBody()
    receipt = await service.archive_definition(
        context=_context(
            tenant=tenant,
            operation=DynamicAppOperation.ARCHIVE_DEFINITION,
            idempotency_key=idempotency_key,
        ),
        request=ArchiveDefinitionRequest(
            definition_id=definition_id, reason=payload.reason
        ),
    )
    return _envelope(receipt.data, trace_id=receipt.trace_id, replayed=receipt.replayed)


# -------------------------------------------------------------------------- versions


@router.get("/{app_id}/versions")
async def list_dynamic_app_versions(
    company_id: str,
    app_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    tenant = await resolve_tenant_context(
        company_id=company_id, principal=principal, session=session, employee=employee
    )
    data = await _service(session).get_definition(
        identity_tenant_id=tenant.tenant_id, app_id=app_id
    )
    items = data["versions"]
    return _envelope(items, count=len(items))


@router.post("/{app_id}/versions", status_code=201)
async def upload_dynamic_app_version(
    company_id: str,
    app_id: str,
    body: CreateVersionBody,
    response: Response,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> dict:
    tenant = await resolve_tenant_context(
        company_id=company_id, principal=principal, session=session, employee=employee
    )
    # Validate the idempotency header before any read so a malformed request is a 400
    # regardless of whether the target exists.
    idempotency_key = _idempotency_key(idempotency_key)
    service = _service(session)
    definition_id = await _definition_id_for(
        service=service, tenant_id=tenant.tenant_id, app_id=app_id
    )
    receipt = await service.create_version(
        context=_context(
            tenant=tenant,
            operation=DynamicAppOperation.CREATE_VERSION,
            idempotency_key=idempotency_key,
        ),
        definition_id=definition_id,
        request=CreateVersionRequest(
            manifest=body.manifest,
            artifact_digest=body.artifact_digest,
            artifact_uri=body.artifact_uri,
        ),
    )
    if receipt.replayed:
        response.status_code = 200
    return _envelope(receipt.data, trace_id=receipt.trace_id, replayed=receipt.replayed)


@router.post("/{app_id}/versions/{version_id}/publish")
async def publish_dynamic_app_version(
    company_id: str,
    app_id: str,
    version_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    body: PublishVersionBody | None = None,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> dict:
    tenant = await resolve_tenant_context(
        company_id=company_id, principal=principal, session=session, employee=employee
    )
    # Validate the idempotency header before any read so a malformed request is a 400
    # regardless of whether the target exists.
    idempotency_key = _idempotency_key(idempotency_key)
    service = _service(session)
    definition_id = await _definition_id_for(
        service=service, tenant_id=tenant.tenant_id, app_id=app_id
    )
    payload = body or PublishVersionBody()
    receipt = await service.publish_version(
        context=_context(
            tenant=tenant,
            operation=DynamicAppOperation.PUBLISH_VERSION,
            idempotency_key=idempotency_key,
        ),
        request=PublishVersionRequest(
            definition_id=definition_id,
            version_id=version_id,
            expected_current_version_id=payload.expected_current_version_id,
        ),
    )
    return _envelope(receipt.data, trace_id=receipt.trace_id, replayed=receipt.replayed)


# ------------------------------------------------------------------------- instances


@router.get("/{app_id}/instances")
async def list_dynamic_app_instances(
    company_id: str,
    app_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    tenant = await resolve_tenant_context(
        company_id=company_id, principal=principal, session=session, employee=employee
    )
    service = _service(session)
    definition_id = await _definition_id_for(
        service=service, tenant_id=tenant.tenant_id, app_id=app_id
    )
    page = await service.list_instances(
        identity_tenant_id=tenant.tenant_id, definition_id=definition_id, limit=limit
    )
    return _envelope(page["items"], count=len(page["items"]))


@router.post("/{app_id}/instances", status_code=201)
async def install_dynamic_app_instance(
    company_id: str,
    app_id: str,
    body: InstallInstanceBody,
    response: Response,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> dict:
    tenant = await resolve_tenant_context(
        company_id=company_id, principal=principal, session=session, employee=employee
    )
    # Validate the idempotency header before any read so a malformed request is a 400
    # regardless of whether the target exists.
    idempotency_key = _idempotency_key(idempotency_key)
    service = _service(session)
    definition_id = await _definition_id_for(
        service=service, tenant_id=tenant.tenant_id, app_id=app_id
    )
    receipt = await service.install_instance(
        context=_context(
            tenant=tenant,
            operation=DynamicAppOperation.INSTALL_INSTANCE,
            idempotency_key=idempotency_key,
        ),
        request=InstallInstanceRequest(
            definition_id=definition_id,
            scope_kind=_scope_kind(body.scope_kind),
            scope_id=body.scope_id,
            version_id=body.version_id,
            runtime_ref=body.runtime_ref,
        ),
    )
    if receipt.replayed:
        response.status_code = 200
    return _envelope(receipt.data, trace_id=receipt.trace_id, replayed=receipt.replayed)


# ---------------------------------------------------------------------------- grants


@router.get("/{app_id}/versions/{version_id}/grants")
async def list_dynamic_app_grants(
    company_id: str,
    app_id: str,
    version_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> dict:
    tenant = await resolve_tenant_context(
        company_id=company_id, principal=principal, session=session, employee=employee
    )
    service = _service(session)
    definition_id = await _definition_id_for(
        service=service, tenant_id=tenant.tenant_id, app_id=app_id
    )
    page = await service.list_grants(
        identity_tenant_id=tenant.tenant_id,
        definition_id=definition_id,
        version_id=version_id,
    )
    return _envelope(page["items"], count=len(page["items"]))


@router.post("/{app_id}/versions/{version_id}/grants", status_code=201)
async def issue_dynamic_app_grant(
    company_id: str,
    app_id: str,
    version_id: str,
    body: IssueGrantBody,
    response: Response,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> dict:
    tenant = await resolve_tenant_context(
        company_id=company_id, principal=principal, session=session, employee=employee
    )
    # Validate the idempotency header before any read so a malformed request is a 400
    # regardless of whether the target exists.
    idempotency_key = _idempotency_key(idempotency_key)
    service = _service(session)
    definition_id = await _definition_id_for(
        service=service, tenant_id=tenant.tenant_id, app_id=app_id
    )
    receipt = await service.issue_grant(
        context=_context(
            tenant=tenant,
            operation=DynamicAppOperation.ISSUE_GRANT,
            idempotency_key=idempotency_key,
        ),
        request=IssueGrantRequest(
            definition_id=definition_id,
            version_id=version_id,
            scope_kind=_scope_kind(body.scope_kind),
            scope_id=body.scope_id,
            capabilities=tuple(
                GrantRequest(
                    name=item.name,
                    decision=CapabilityDecision(item.decision),
                    constraints=item.constraints,
                    expires_at=item.expires_at,
                )
                for item in body.capabilities
            ),
            expires_at=body.expires_at,
        ),
    )
    if receipt.replayed:
        response.status_code = 200
    return _envelope(receipt.data, trace_id=receipt.trace_id, replayed=receipt.replayed)


@router.post("/{app_id}/versions/{version_id}/grants/{capability}/revoke")
async def revoke_dynamic_app_grant(
    company_id: str,
    app_id: str,
    version_id: str,
    capability: str,
    body: RevokeGrantBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> dict:
    tenant = await resolve_tenant_context(
        company_id=company_id, principal=principal, session=session, employee=employee
    )
    # Validate the idempotency header before any read so a malformed request is a 400
    # regardless of whether the target exists.
    idempotency_key = _idempotency_key(idempotency_key)
    service = _service(session)
    definition_id = await _definition_id_for(
        service=service, tenant_id=tenant.tenant_id, app_id=app_id
    )
    receipt = await service.revoke_grant(
        context=_context(
            tenant=tenant,
            operation=DynamicAppOperation.REVOKE_GRANT,
            idempotency_key=idempotency_key,
        ),
        request=RevokeGrantRequest(
            definition_id=definition_id,
            version_id=version_id,
            scope_kind=_scope_kind(body.scope_kind),
            scope_id=body.scope_id,
            capability=capability,
            reason=body.reason,
        ),
    )
    return _envelope(receipt.data, trace_id=receipt.trace_id, replayed=receipt.replayed)


def _lifecycle_filter(value: str | None):
    """Map the public ``archived`` filter onto the persisted ``disabled`` value."""
    if value is None:
        return None
    if value == "archived":
        return archived_persisted_status()
    return AppLifecycleStatus(value)


__all__ = ["TenantContext", "resolve_tenant_context", "router"]
