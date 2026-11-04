"""Content Service HTTP — assets, immutable versions, aliases, ACL, links.

Authoritative contract: ``apps/api/src/prodavan/application/content/CONTRACT.md``.
Pure policy constants and blockers: ``application/content/contracts.py``.

Hard rules enforced by this router (not by the client):

* Tenant and scope come **only** from the verified principal plus persisted
  relations (``application/content/scope.py``). ``owner_company_id``,
  ``X-Cabinet-Id`` and ``X-Project-Id`` are *selectors* validated against that
  server-derived set; a foreign tenant is ``404`` so existence never leaks.
* Every operation runs an ACL check (``AccessPolicyService``). Same-tenant but
  ungranted is ``403``; other-tenant is ``404``.
* A blob version is immutable: its digest is sealed once and can never change.
  New bytes require a new version row (``409 CONTENT_VERSION_SEALED``).
* ``POST`` create operations honour ``Idempotency-Key``: same key + same body
  replays the original resource, same key + different body is
  ``409 IDEMPOTENCY_REPLAY``.
* A staged (unsealed) version can be aborted (``DELETE .../versions/{vid}``);
  a sealed one cannot, because its digest is the evidence.
* ``DELETE /content/assets/{id}`` fails closed: there is no archive column
  (``409 CONTENT_ARCHIVE_UNAVAILABLE``) and destructive purge is platform-admin
  only and refuses assets with any alias binding history or live entity links
  (``409 CONTENT_DELETE_HAS_DEPENDENTS``). ``GET
  /content/assets/{id}/dependents`` exposes the same evidence read-only.
* ``DELETE /content/aliases/{id}`` disables the permalink instead of deleting the
  row, so binding history survives; ``PATCH status=active`` re-enables it.

Response bodies stay flat (``{"items": [...]}``, single resource object) because
the shipped web client and the module/document adapters already read that shape.
Errors are RFC 7807 problem+json via ``domain.errors.AppError``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import RedirectResponse, Response

from prodavan.api.deps import (
    PrincipalDep,
    SessionDep,
    WorkContextDep,
    get_current_employee,
)
from prodavan.application.content.access_policy import AccessPolicyService
from prodavan.application.content.alias_binding_service import AliasBindingService
from prodavan.application.content.alias_service import AliasService, alias_public
from prodavan.application.content.asset_link_service import AssetLinkService
from prodavan.application.content.asset_service import AssetService
from prodavan.application.content.contracts import (
    MAX_UPLOAD_BYTES,
    VERSION_PAGE_DEFAULT,
    VERSION_PAGE_MAX,
    ContentErrorCode,
)
from prodavan.application.content.download_service import DownloadService, safe_filename
from prodavan.application.content.schemas import (
    AclEntryBody,
    AclList,
    AliasBindingList,
    AliasResource,
    AssetDependents,
    AssetLink,
    AssetLinkList,
    AssetList,
    AssetResource,
    BeginVersionBody,
    BeginVersionResponse,
    BindAliasBody,
    CreateAliasBody,
    CreateAssetBody,
    CreateAssetLinkBody,
    PatchAliasBody,
    PatchAssetBody,
    ReplaceAclBody,
    VersionAbortResult,
    VersionList,
    VersionRead,
    VersionResource,
)
from prodavan.application.content.scope import resolve_actor_scope
from prodavan.domain.content.types import ContentResourceKind
from prodavan.domain.errors import AppError
from prodavan.infrastructure.files.manager import ensure_file_store
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

router = APIRouter(prefix="/content", tags=["content"])

EmployeeDep = Annotated[EmployeeRow | None, Depends(get_current_employee)]
_IDEMPOTENCY_HEADER = "Idempotency-Key"
_SHA256_HEADER = "X-Content-SHA256"


def _content_disposition(filename: str | None) -> str | None:
    safe = safe_filename(filename)
    if not safe:
        return None
    ascii_name = safe.encode("ascii", "ignore").decode("ascii") or "download"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{_quote(safe)}"


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


async def _download_response(
    target,
    *,
    proxy: bool = False,
    include_filename: bool = True,
) -> Response:
    headers: dict[str, str] = {}
    if include_filename:
        disposition = _content_disposition(target.filename)
        if disposition:
            headers["Content-Disposition"] = disposition
    if target.sha256:
        headers["X-Content-SHA256"] = target.sha256
    if target.size is not None:
        headers["X-Content-Size"] = str(int(target.size))
    if target.url and not proxy:
        return RedirectResponse(url=target.url, status_code=302, headers=headers)
    if target.storage_key is None:
        # Never assert here: assertions vanish under ``python -O`` and would
        # then crash the request with a bare AttributeError.
        raise AppError(
            code=ContentErrorCode.NOT_READY,
            title="Not Ready",
            status=422,
            detail="download target has neither a redirect nor stored bytes",
        )
    raw = await ensure_file_store().get_bytes(target.storage_key)
    media = target.content_type or "application/octet-stream"
    return Response(content=raw, media_type=media, headers=headers)


# --- assets -----------------------------------------------------------------


@router.get("/assets", response_model=AssetList)
async def list_assets(
    company_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> AssetList:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    payload = await AssetService(session).list_for_company(
        company_id=company_id,
        principal=principal,
        employee=employee,
        limit=limit,
        cursor=cursor,
        scope=scope,
    )
    return AssetList.model_validate(payload)


@router.post("/assets", response_model=AssetResource)
async def create_asset(
    body: CreateAssetBody,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> AssetResource:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    payload = await AssetService(session).create(
        principal=principal,
        employee=employee,
        owner_company_id=body.owner_company_id,
        visibility=body.visibility,
        mime=body.mime,
        title=body.title,
        tags=body.tags,
        scope=scope,
        idempotency_key=idempotency_key,
    )
    return AssetResource.model_validate(payload)


@router.get("/assets/{asset_id}", response_model=AssetResource)
async def get_asset(
    asset_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
) -> AssetResource:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    payload = await AssetService(session).get(
        asset_id=asset_id, principal=principal, employee=employee, scope=scope
    )
    return AssetResource.model_validate(payload)


@router.patch("/assets/{asset_id}", response_model=AssetResource)
async def patch_asset(
    asset_id: str,
    body: PatchAssetBody,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
) -> AssetResource:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    payload = await AssetService(session).patch(
        asset_id=asset_id,
        principal=principal,
        employee=employee,
        title=body.title,
        tags=body.tags,
        visibility=body.visibility,
        scope=scope,
    )
    return AssetResource.model_validate(payload)


@router.delete("/assets/{asset_id}")
async def delete_asset(
    asset_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
    purge: bool = Query(default=False),
    cascade: bool = Query(default=False),
) -> dict:
    """Archive (default) or purge (``?purge=true``, platform admin only).

    Archiving is blocked by schema — see ``contracts.ARCHIVE_BLOCKER``. The
    default therefore fails closed with ``409 CONTENT_ARCHIVE_UNAVAILABLE``
    instead of destroying rows or silently doing nothing.
    """
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    service = AssetService(session)
    if not purge:
        return await service.archive(
            asset_id=asset_id, principal=principal, employee=employee, scope=scope
        )
    return await service.purge(
        asset_id=asset_id,
        principal=principal,
        employee=employee,
        cascade=cascade,
        scope=scope,
    )


@router.get("/assets/{asset_id}/dependents", response_model=AssetDependents)
async def get_asset_dependents(
    asset_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
) -> AssetDependents:
    """Evidence for the delete decision: bindings, links, version count.

    Archive is still blocked by the missing ``archived_at`` column, so this is
    how a caller inspects what a destructive purge would discard. Requires the
    same ``admin`` access as ``DELETE`` and is tenant-scoped (a foreign asset is
    ``404``).
    """
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    payload = await AssetService(session).delete_evidence(
        asset_id=asset_id, principal=principal, employee=employee, scope=scope
    )
    return AssetDependents.model_validate(payload)


# --- versions ---------------------------------------------------------------


@router.get("/assets/{asset_id}/versions", response_model=VersionList)
async def list_asset_versions(
    asset_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
    limit: int = Query(default=VERSION_PAGE_DEFAULT, ge=1, le=VERSION_PAGE_MAX),
    cursor: str | None = Query(default=None),
) -> VersionList:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    payload = await AssetService(session).list_versions(
        asset_id=asset_id,
        principal=principal,
        employee=employee,
        limit=limit,
        cursor=cursor,
        scope=scope,
    )
    return VersionList.model_validate({**payload, "limit": limit})


@router.get("/assets/{asset_id}/versions/{version_id}", response_model=VersionRead)
async def get_asset_version(
    asset_id: str,
    version_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
) -> VersionRead:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    payload = await AssetService(session).get_version(
        asset_id=asset_id,
        version_id=version_id,
        principal=principal,
        employee=employee,
        scope=scope,
    )
    return VersionRead.model_validate(payload)


@router.post("/assets/{asset_id}/versions", response_model=BeginVersionResponse)
async def begin_asset_version(
    asset_id: str,
    body: BeginVersionBody,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
) -> BeginVersionResponse:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    payload = await AssetService(session).begin_version_upload(
        asset_id=asset_id,
        principal=principal,
        employee=employee,
        mime=body.mime,
        ttl_seconds=body.ttl_seconds,
        declared_sha256=body.sha256,
        scope=scope,
    )
    return BeginVersionResponse.model_validate(payload)


@router.put("/assets/{asset_id}/versions/{version_id}/upload", response_model=VersionResource)
async def upload_asset_version(
    asset_id: str,
    version_id: str,
    request: Request,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
    expected_sha256: Annotated[str | None, Header(alias=_SHA256_HEADER)] = None,
) -> VersionResource:
    """Upload version bytes through the API for clients outside the cluster."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            raise AppError(
                code=ContentErrorCode.CONTENT_UPLOAD_TOO_LARGE,
                title="Payload too large",
                status=413,
                detail="upload exceeds 100 MiB limit",
            )
        chunks.append(chunk)
    data = b"".join(chunks)
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    payload = await AssetService(session).upload_version_bytes(
        asset_id=asset_id,
        version_id=version_id,
        data=data,
        principal=principal,
        employee=employee,
        mime=request.headers.get("content-type"),
        expected_sha256=expected_sha256,
        scope=scope,
    )
    return VersionResource.model_validate(payload)


@router.post("/assets/{asset_id}/versions/{version_id}/finalize", response_model=VersionResource)
async def finalize_asset_version(
    asset_id: str,
    version_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
    expected_sha256: Annotated[str | None, Header(alias=_SHA256_HEADER)] = None,
) -> VersionResource:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    payload = await AssetService(session).finalize_version(
        asset_id=asset_id,
        version_id=version_id,
        principal=principal,
        employee=employee,
        expected_sha256=expected_sha256,
        scope=scope,
    )
    return VersionResource.model_validate(payload)


@router.delete("/assets/{asset_id}/versions/{version_id}", response_model=VersionAbortResult)
async def abort_asset_version(
    asset_id: str,
    version_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
) -> VersionAbortResult:
    """Abort a staged (unsealed) upload.

    The web client stages a version before the bytes are confirmed; without this
    route a cancelled upload left an unusable version row behind. A **sealed**
    version is refused with ``409 CONTENT_VERSION_SEALED`` — the digest is
    immutable evidence and new bytes require a new version.
    """
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    payload = await AssetService(session).abort_version(
        asset_id=asset_id,
        version_id=version_id,
        principal=principal,
        employee=employee,
        scope=scope,
    )
    return VersionAbortResult.model_validate(payload)


@router.get("/assets/{asset_id}/download")
async def download_asset(
    asset_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
    blob_version_id: str | None = Query(default=None),
    proxy: bool = Query(default=False),
) -> Response:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    target = await DownloadService(session).presign_asset(
        asset_id=asset_id,
        principal=principal,
        employee=employee,
        blob_version_id=blob_version_id,
        scope=scope,
    )
    # ``proxy=1``: stream the bytes through the API instead of the 302 to the
    # presigned blob URL. Desktop/mobile clients outside the cluster cannot
    # resolve the in-cluster object-store DNS (dev: prodavan-minio:9000), so
    # module-export downloads (budget xlsx / КП PDF) use this mode.
    return await _download_response(target, proxy=proxy)


# --- asset links ------------------------------------------------------------


@router.get("/assets/{asset_id}/links", response_model=AssetLinkList)
async def list_asset_links(
    asset_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
) -> AssetLinkList:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    items = await AssetLinkService(session).list_for_asset(
        asset_id=asset_id, principal=principal, employee=employee, scope=scope
    )
    return AssetLinkList(items=[AssetLink.model_validate(item) for item in items])


@router.post("/assets/{asset_id}/links", response_model=AssetLink)
async def create_asset_link(
    asset_id: str,
    body: CreateAssetLinkBody,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
) -> AssetLink:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    payload = await AssetLinkService(session).link(
        asset_id=asset_id,
        link_kind=body.link_kind,
        link_id=body.link_id,
        principal=principal,
        employee=employee,
        replace=body.replace,
        scope=scope,
    )
    return AssetLink.model_validate(payload)


@router.delete("/assets/{asset_id}/links/{link_id}")
async def delete_asset_link(
    asset_id: str,
    link_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
) -> dict:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    return await AssetLinkService(session).unlink(
        asset_id=asset_id,
        link_id=link_id,
        principal=principal,
        employee=employee,
        scope=scope,
    )


# --- aliases ----------------------------------------------------------------


@router.get("/aliases")
async def list_aliases(
    company_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> dict:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    return await AliasService(session).list_for_company(
        company_id=company_id,
        principal=principal,
        employee=employee,
        limit=limit,
        cursor=cursor,
        scope=scope,
    )


@router.post("/aliases", response_model=AliasResource)
async def create_alias(
    body: CreateAliasBody,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
    idempotency_key: Annotated[str | None, Header(alias=_IDEMPOTENCY_HEADER)] = None,
) -> AliasResource:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    payload = await AliasService(session).create(
        slug=body.slug,
        principal=principal,
        employee=employee,
        owner_company_id=body.owner_company_id,
        visibility=body.visibility,
        label=body.label,
        description=body.description,
        metadata=body.metadata,
        scope=scope,
        idempotency_key=idempotency_key,
    )
    return AliasResource.model_validate(payload)


@router.get("/aliases/{alias_id_or_slug}", response_model=AliasResource)
async def get_alias(
    alias_id_or_slug: str,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
) -> AliasResource:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    payload = await AliasService(session).get_by_id_or_slug(
        alias_id_or_slug=alias_id_or_slug,
        principal=principal,
        employee=employee,
        scope=scope,
    )
    return AliasResource.model_validate(payload)


@router.patch("/aliases/{alias_id}", response_model=AliasResource)
async def patch_alias(
    alias_id: str,
    body: PatchAliasBody,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
) -> AliasResource:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    payload = await AliasService(session).patch(
        alias_id=alias_id,
        principal=principal,
        employee=employee,
        label=body.label,
        description=body.description,
        metadata=body.metadata,
        status=body.status,
        visibility=body.visibility,
        scope=scope,
    )
    return AliasResource.model_validate(payload)


@router.delete("/aliases/{alias_id}")
async def delete_alias(
    alias_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
) -> dict:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    return await AliasService(session).delete(
        alias_id=alias_id, principal=principal, employee=employee, scope=scope
    )


@router.post("/aliases/{alias_id}/bind")
async def bind_alias(
    alias_id: str,
    body: BindAliasBody,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
) -> dict:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    return await AliasBindingService(session).bind(
        alias_id=alias_id,
        asset_id=body.asset_id,
        blob_version_id=body.blob_version_id,
        principal=principal,
        employee=employee,
        scope=scope,
    )


@router.delete("/aliases/{alias_id}/bind")
async def unbind_alias(
    alias_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
) -> dict:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    return await AliasBindingService(session).unbind(
        alias_id=alias_id, principal=principal, employee=employee, scope=scope
    )


@router.get("/aliases/{alias_id}/bindings", response_model=AliasBindingList)
async def alias_bindings(
    alias_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> AliasBindingList:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    # Binding history is retained evidence: it stays readable after ``DELETE``
    # disabled the alias, so this uses management access (write/admin) instead
    # of the active-only read gate used by resolve/download.
    alias_row = await AliasService(session).require_management_row(
        alias_id=alias_id, principal=principal, employee=employee, scope=scope
    )
    items = await AliasBindingService(session).history(
        alias_id,
        limit=limit,
        principal=principal,
        employee=employee,
        scope=scope,
    )
    return AliasBindingList.model_validate({"alias": alias_public(alias_row), "items": items})


@router.get("/aliases/{slug}/resolve")
async def resolve_alias(
    slug: str,
    principal: PrincipalDep,
    session: SessionDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
    proxy: bool = Query(default=False),
) -> Response:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    target = await DownloadService(session).resolve_alias_slug(
        slug=slug, principal=principal, employee=employee, scope=scope
    )
    return await _download_response(target, proxy=proxy)


# --- ACL --------------------------------------------------------------------


@router.get("/assets/{asset_id}/acl", response_model=AclList)
async def get_asset_acl(
    asset_id: str,
    session: SessionDep,
    principal: PrincipalDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
) -> AclList:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    entries = await AccessPolicyService(session).list_acl(
        resource_kind=ContentResourceKind.ASSET,
        resource_id=asset_id,
        principal=principal,
        employee=employee,
        scope=scope,
    )
    return AclList(
        resource_kind=str(ContentResourceKind.ASSET),
        resource_id=asset_id,
        entries=entries,
    )


@router.put("/assets/{asset_id}/acl", response_model=AclList)
async def put_asset_acl(
    asset_id: str,
    body: ReplaceAclBody,
    session: SessionDep,
    principal: PrincipalDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
) -> AclList:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    entries = await AccessPolicyService(session).replace_acl(
        resource_kind=ContentResourceKind.ASSET,
        resource_id=asset_id,
        entries=[_acl_entry(e) for e in body.entries],
        principal=principal,
        employee=employee,
        scope=scope,
    )
    return AclList(
        resource_kind=str(ContentResourceKind.ASSET),
        resource_id=asset_id,
        entries=entries,
    )


@router.get("/aliases/{alias_id}/acl", response_model=AclList)
async def get_alias_acl(
    alias_id: str,
    session: SessionDep,
    principal: PrincipalDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
) -> AclList:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    entries = await AccessPolicyService(session).list_acl(
        resource_kind=ContentResourceKind.ALIAS,
        resource_id=alias_id,
        principal=principal,
        employee=employee,
        scope=scope,
    )
    return AclList(
        resource_kind=str(ContentResourceKind.ALIAS),
        resource_id=alias_id,
        entries=entries,
    )


@router.put("/aliases/{alias_id}/acl", response_model=AclList)
async def put_alias_acl(
    alias_id: str,
    body: ReplaceAclBody,
    session: SessionDep,
    principal: PrincipalDep,
    work: WorkContextDep,
    employee: EmployeeDep = None,
) -> AclList:
    scope = await resolve_actor_scope(
        session,
        principal=principal,
        employee=employee,
        cabinet_id=work.cabinet_id,
        project_id=work.project_id,
    )
    entries = await AccessPolicyService(session).replace_acl(
        resource_kind=ContentResourceKind.ALIAS,
        resource_id=alias_id,
        entries=[_acl_entry(e) for e in body.entries],
        principal=principal,
        employee=employee,
        scope=scope,
    )
    return AclList(
        resource_kind=str(ContentResourceKind.ALIAS),
        resource_id=alias_id,
        entries=entries,
    )


def _acl_entry(entry: AclEntryBody) -> dict[str, str]:
    return {
        "principal_kind": entry.principal_kind,
        "principal_id": entry.principal_id,
        "permission": entry.permission,
    }
