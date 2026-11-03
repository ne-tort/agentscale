"""Canonical Resource API — HTTP surface (Task MIG-RESOURCE-API-02).

Prompts, files and MCP servers share one resource model: a stable root, an
append-only list of immutable digest-bound versions, and explicit bindings to a
project, a cabinet or a module. This router is the only HTTP entry point; all
policy lives in ``prodavan.application.resources.api``.

Hard rules enforced here (never delegated to the client)
--------------------------------------------------------
* **Tenant and scope come from the server.** The ``{company_id}`` path segment is
  an assertion resolved through ``EntitlementService``; the resolved company id
  becomes ``tenant_id``. A cabinet, project or module is proven inside that
  tenant by ``ResourceScopeService``; a foreign scope is answered ``404`` so
  existence never leaks across tenants.
* **Versions are immutable.** A published payload is digest-bound; new bytes
  require a new version row (``409 RESOURCE_VERSION_IMMUTABLE`` /
  ``RESOURCE_VERSION_STALE``). Publishing routes through
  ``Resource.publish_version`` from the MIG-RES-01 contracts, never through a
  local transition table.
* **Bindings pin a published version.** Binding an archived, draft or
  quarantined resource is ``409 RESOURCE_NOT_BINDABLE``; a binding whose target
  is outside the tenant is ``404 RESOURCE_BINDING_CROSS_TENANT``.
* **``Idempotency-Key`` is required for every mutation.** Creation, version
  append, binding, archive, delete and materialization apply each read the
  header before any side effect: an exact repeat returns the original result
  with ``replayed: true`` (``200`` instead of ``201``), the same key with a
  different body is ``409 IDEMPOTENCY_REPLAY``. Publish and archive transitions
  are state-convergent: repeating them returns the same resulting state.
* **Archiving preserves evidence.** There is no hard delete: ``DELETE`` and
  ``POST /archive`` perform the contract's terminal ``archived`` transition while
  keeping the root, every immutable version and every binding row addressable.
* **``BLOCKED`` never applies.** A plan with blockers, an incomplete resolution,
  a missing CAS boundary or a missing receipt store is refused with
  ``RESOURCE_APPLY_BLOCKED`` / ``RESOURCE_APPLY_UNAVAILABLE``; a stale plan is
  ``409 RESOURCE_PLAN_STALE``. Planning itself is delegated to
  ``ResourceMaterializationService`` — this module never re-plans operations.
* **Stable error codes.** Every rejection is an ``AppError`` carrying a code from
  ``ResourceApiErrorCode``; response bodies stay flat envelopes
  (``{"data": ..., "meta": {...}}``) so the web client reads one shape.

Persistence adapters are registered process-wide through
``application.resources.api.composition``; until that happens every route fails
closed with ``503 RESOURCE_STORE_UNAVAILABLE`` rather than serving memory state.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, Field

from prodavan.api.deps import PrincipalDep, SessionDep, get_current_employee
from prodavan.application.identity.service import EntitlementService
from prodavan.application.resources.api import (
    ResolvedTenant,
    ResourceApiBackend,
    ScopeRef,
    build_resource_api,
    require_idempotency_key,
)
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

router = APIRouter(prefix="/companies/{company_id}", tags=["resources"])

_IDEMPOTENCY_HEADER = "Idempotency-Key"
EmployeeDep = Annotated[EmployeeRow | None, Depends(get_current_employee)]


# --------------------------------------------------------------------------- bodies


class ScopeBody(BaseModel):
    """A typed scope reference; the server proves it belongs to the tenant."""

    model_config = {"extra": "forbid"}

    kind: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    id: str = Field(min_length=1, max_length=80)

    def ref(self) -> ScopeRef:
        return ScopeRef(kind=self.kind, scope_id=self.id)


class CreateResourceBody(BaseModel):
    model_config = {"extra": "forbid"}

    kind: str = Field(min_length=1, max_length=32)
    slug: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=260)
    description: str = Field(default="", max_length=4000)
    scope: ScopeBody
    payload: dict[str, Any]
    label: str = Field(default="", max_length=200)
    change_note: str = Field(default="", max_length=2000)
    metadata: dict[str, Any] | None = None


class CreateVersionBody(BaseModel):
    model_config = {"extra": "forbid"}

    payload: dict[str, Any]
    label: str = Field(default="", max_length=200)
    change_note: str = Field(default="", max_length=2000)
    expected_active_version: int | None = Field(default=None, ge=1)


class PublishVersionBody(BaseModel):
    model_config = {"extra": "forbid"}

    expected_active_version: int | None = Field(default=None, ge=1)


class ArchiveResourceBody(BaseModel):
    model_config = {"extra": "forbid"}

    expected_active_version: int | None = Field(default=None, ge=1)


class CreateBindingBody(BaseModel):
    model_config = {"extra": "forbid"}

    target: ScopeBody
    mode: str = Field(default="global", min_length=1, max_length=16)
    version_number: int | None = Field(default=None, ge=1)
    child_may_edit: bool = False
    project_ids: list[str] = Field(default_factory=list, max_length=64)
    workspace_path: str | None = Field(default=None, max_length=1024)


class PlanMaterializationBody(BaseModel):
    model_config = {"extra": "forbid"}

    expected_generation: int | None = Field(default=None, ge=0)


class ApplyMaterializationBody(BaseModel):
    model_config = {"extra": "forbid"}

    #: The plan identity returned by the plan endpoint. Planning is never
    #: trusted from the body: the server rebuilds the plan and compares ids.
    plan_id: str = Field(min_length=1, max_length=200)
    #: The generation the plan advances to (``data.desired_generation``).
    expected_generation: int = Field(ge=1)


# ---------------------------------------------------------------------- dependencies


async def resource_tenant(
    company_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: EmployeeDep,
) -> ResolvedTenant:
    """Resolve the tenant from the verified principal, never from the payload."""
    company = await EntitlementService(session).require_company_actor(
        principal, company_id, employee=employee
    )
    actor_id = str(employee.id) if employee is not None else principal.sub
    return ResolvedTenant(
        tenant_id=company.id,
        actor_id=actor_id,
        is_platform_admin=principal.is_platform_admin,
    )


async def get_resource_api(session: SessionDep) -> ResourceApiBackend:
    """Request-scoped services bound to the registered persistence adapters."""
    return build_resource_api(session)


ResourceTenantDep = Annotated[ResolvedTenant, Depends(resource_tenant)]
ResourceApiDep = Annotated[ResourceApiBackend, Depends(get_resource_api)]


# --------------------------------------------------------------------------- helpers


def _envelope(data: Any, **meta: Any) -> dict[str, Any]:
    return {"data": data, "meta": {key: value for key, value in meta.items() if value is not None}}


def _item(view: Any, **meta: Any) -> dict[str, Any]:
    payload = view.as_payload() if hasattr(view, "as_payload") else dict(view)
    return _envelope(payload, **meta)


def _collection(views: Iterable[Any], *, limit: int | None = None, **meta: Any) -> dict[str, Any]:
    payload = [view.as_payload() for view in views]
    next_after_id = None
    if limit is not None and len(payload) >= limit and payload:
        next_after_id = payload[-1].get("id")
    return _envelope(payload, count=len(payload), next_after_id=next_after_id, **meta)


def _scope_ref(body: ScopeBody) -> ScopeRef:
    return body.ref()


def _project_ref(project_id: str) -> ScopeRef:
    return ScopeRef(kind="project", scope_id=project_id)


# --------------------------------------------------------------------------- catalog


@router.get("/resources")
async def list_resources(
    tenant: ResourceTenantDep,
    api: ResourceApiDep,
    kind: str | None = Query(default=None, max_length=32),
    lifecycle: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
    after_id: str | None = Query(default=None, max_length=80),
) -> dict[str, Any]:
    """List resources owned inside the request tenant.

    ``kind`` and ``lifecycle`` are validated by the application layer so an
    unsupported filter yields the stable resource code, not a generic 422.
    """
    views = await api.commands.list_resources(
        tenant,
        kind=kind,
        lifecycle=lifecycle,
        limit=limit,
        after_id=after_id,
    )
    return _collection(views, limit=limit)


@router.post("/resources", status_code=201)
async def create_resource(
    body: CreateResourceBody,
    response: Response,
    tenant: ResourceTenantDep,
    api: ResourceApiDep,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> dict[str, Any]:
    """Create a resource root plus its first immutable draft version."""
    key = require_idempotency_key(idempotency_key)
    view = await api.commands.create_resource(
        tenant,
        kind=body.kind,
        slug=body.slug,
        title=body.title,
        description=body.description,
        scope=_scope_ref(body.scope),
        payload=body.payload,
        label=body.label,
        change_note=body.change_note,
        metadata=body.metadata,
        idempotency_key=key,
    )
    if view.replayed:
        response.status_code = 200
    return _item(view, replayed=view.replayed)


@router.get("/resources/{resource_id}")
async def get_resource(
    resource_id: str,
    tenant: ResourceTenantDep,
    api: ResourceApiDep,
) -> dict[str, Any]:
    return _item(await api.commands.get_resource(tenant, resource_id))


@router.post("/resources/{resource_id}/archive")
async def archive_resource(
    resource_id: str,
    tenant: ResourceTenantDep,
    api: ResourceApiDep,
    body: ArchiveResourceBody | None = None,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> dict[str, Any]:
    """Terminal ``archived`` transition; versions and bindings stay as evidence."""
    require_idempotency_key(idempotency_key)
    payload = body or ArchiveResourceBody()
    view = await api.commands.archive(
        tenant,
        resource_id,
        expected_active_version=payload.expected_active_version,
    )
    return _item(view, archived=True)


@router.delete("/resources/{resource_id}")
async def delete_resource(
    resource_id: str,
    tenant: ResourceTenantDep,
    api: ResourceApiDep,
    expected_active_version: int | None = Query(default=None, ge=1),
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> dict[str, Any]:
    """Archive instead of hard delete: the row and its history remain audit evidence.

    Repeating the call is state-convergent (the resource is already ``archived``)
    and returns the same payload, so the terminal state is never rewritten.
    """
    require_idempotency_key(idempotency_key)
    view = await api.commands.archive(
        tenant,
        resource_id,
        expected_active_version=expected_active_version,
    )
    return _item(view, archived=True, hard_deleted=False)


# -------------------------------------------------------------------------- versions


@router.get("/resources/{resource_id}/versions")
async def list_resource_versions(
    resource_id: str,
    tenant: ResourceTenantDep,
    api: ResourceApiDep,
) -> dict[str, Any]:
    views = await api.commands.list_versions(tenant, resource_id)
    return _collection(views)


@router.post("/resources/{resource_id}/versions", status_code=201)
async def create_resource_version(
    resource_id: str,
    body: CreateVersionBody,
    response: Response,
    tenant: ResourceTenantDep,
    api: ResourceApiDep,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> dict[str, Any]:
    """Append an immutable version; the digest is derived server-side."""
    key = require_idempotency_key(idempotency_key)
    view = await api.commands.create_version(
        tenant,
        resource_id,
        payload=body.payload,
        label=body.label,
        change_note=body.change_note,
        expected_active_version=body.expected_active_version,
        idempotency_key=key,
    )
    if view.replayed:
        response.status_code = 200
    return _item(view, replayed=view.replayed)


@router.get("/resources/{resource_id}/versions/{number}")
async def get_resource_version(
    resource_id: str,
    number: int,
    tenant: ResourceTenantDep,
    api: ResourceApiDep,
) -> dict[str, Any]:
    return _item(await api.commands.get_version(tenant, resource_id, number))


@router.post("/resources/{resource_id}/versions/{number}/publish")
async def publish_resource_version(
    resource_id: str,
    number: int,
    tenant: ResourceTenantDep,
    api: ResourceApiDep,
    body: PublishVersionBody | None = None,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> dict[str, Any]:
    """Publish one version and retire the others through the lifecycle contract."""
    require_idempotency_key(idempotency_key)
    payload = body or PublishVersionBody()
    view = await api.commands.publish_version(
        tenant,
        resource_id,
        number,
        expected_active_version=payload.expected_active_version,
    )
    return _item(view, active_version=view.active_version)


# -------------------------------------------------------------------------- bindings


@router.get("/resources/{resource_id}/bindings")
async def list_resource_bindings(
    resource_id: str,
    tenant: ResourceTenantDep,
    api: ResourceApiDep,
) -> dict[str, Any]:
    views = await api.commands.list_bindings(tenant, resource_id)
    return _collection(views)


@router.post("/resources/{resource_id}/bindings", status_code=201)
async def create_resource_binding(
    resource_id: str,
    body: CreateBindingBody,
    response: Response,
    tenant: ResourceTenantDep,
    api: ResourceApiDep,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> dict[str, Any]:
    """Bind the active published version to a proven project/cabinet/module target."""
    key = require_idempotency_key(idempotency_key)
    view = await api.commands.create_binding(
        tenant,
        resource_id,
        target=_scope_ref(body.target),
        mode=body.mode,
        version_number=body.version_number,
        child_may_edit=body.child_may_edit,
        project_ids=tuple(body.project_ids),
        workspace_path=body.workspace_path,
        idempotency_key=key,
    )
    if view.replayed:
        response.status_code = 200
    return _item(view, replayed=view.replayed)


@router.delete("/resources/{resource_id}/bindings/{binding_id}")
async def delete_resource_binding(
    resource_id: str,
    binding_id: str,
    tenant: ResourceTenantDep,
    api: ResourceApiDep,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> dict[str, Any]:
    """Remove one edge. Versions and the resource root are untouched."""
    require_idempotency_key(idempotency_key)
    deleted = await api.commands.delete_binding(tenant, resource_id, binding_id)
    return _envelope(deleted)

@router.get("/projects/{project_id}/resources/bindings")
async def list_project_resource_bindings(
    project_id: str,
    tenant: ResourceTenantDep,
    api: ResourceApiDep,
) -> dict[str, Any]:
    """Every binding that targets this project, proven inside the tenant."""
    views = await api.commands.list_project_bindings(tenant, project_id)
    return _collection(views)


# ------------------------------------------------------------------- materialization


@router.post("/projects/{project_id}/resources/materialization/plans")
async def plan_project_resource_materialization(
    project_id: str,
    tenant: ResourceTenantDep,
    api: ResourceApiDep,
    body: PlanMaterializationBody | None = None,
) -> dict[str, Any]:
    """Preview the plan for the project's bound resources (read-only).

    The plan id is derived from observable state; nothing is written and no
    selection is accepted from the client.
    """
    payload = body or PlanMaterializationBody()
    view = await api.materialization.plan(
        tenant,
        _project_ref(project_id),
        expected_generation=payload.expected_generation,
    )
    return _item(view, ready=view.ready, blockers=list(view.blockers))


@router.post("/projects/{project_id}/resources/materialization/applies", status_code=201)
async def apply_project_resource_materialization(
    project_id: str,
    body: ApplyMaterializationBody,
    response: Response,
    tenant: ResourceTenantDep,
    api: ResourceApiDep,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> dict[str, Any]:
    """Apply the current plan exactly once per idempotency key.

    The plan is rebuilt from the project's bindings and compared with
    ``body.plan_id``; a changed selection is stale, and a ``BLOCKED`` plan never
    reaches the CAS boundary. Repeating the request with the same key replays the
    stored receipt (``200`` + ``meta.idempotent_replay``).
    """
    key = require_idempotency_key(idempotency_key)
    view = await api.materialization.apply_plan(
        tenant,
        _project_ref(project_id),
        plan_id=body.plan_id,
        expected_generation=body.expected_generation,
        idempotency_key=key,
    )
    if view.idempotent_replay:
        response.status_code = 200
    return _item(view, idempotent_replay=view.idempotent_replay)


__all__ = [
    "ApplyMaterializationBody",
    "ArchiveResourceBody",
    "CreateBindingBody",
    "CreateResourceBody",
    "CreateVersionBody",
    "PlanMaterializationBody",
    "PublishVersionBody",
    "ResourceApiDep",
    "ResourceTenantDep",
    "ScopeBody",
    "get_resource_api",
    "resource_tenant",
    "router",
]
