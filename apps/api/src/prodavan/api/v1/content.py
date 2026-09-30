"""Content Service HTTP — assets, aliases, ACL, resolve."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field

from prodavan.api.deps import PrincipalDep, SessionDep, get_current_employee
from prodavan.application.content.access_policy import AccessPolicyService
from prodavan.application.content.alias_binding_service import AliasBindingService
from prodavan.application.content.alias_service import AliasService
from prodavan.application.content.asset_service import AssetService
from prodavan.application.content.download_service import DownloadService
from prodavan.domain.content.types import ContentResourceKind
from prodavan.infrastructure.files.manager import ensure_file_store
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

router = APIRouter(prefix="/content", tags=["content"])


class CreateAssetBody(BaseModel):
    model_config = {"extra": "forbid"}

    owner_company_id: str | None = None
    visibility: str = "company"
    mime: str | None = None
    title: str | None = None
    tags: list[str] = Field(default_factory=list)


class PatchAssetBody(BaseModel):
    model_config = {"extra": "forbid"}

    title: str | None = None
    tags: list[str] | None = None
    visibility: str | None = None


class BeginVersionBody(BaseModel):
    model_config = {"extra": "forbid"}

    mime: str | None = None
    ttl_seconds: int = Field(default=900, ge=60, le=3600)


class CreateAliasBody(BaseModel):
    model_config = {"extra": "forbid"}

    slug: str = Field(min_length=3, max_length=128)
    owner_company_id: str | None = None
    visibility: str = "company"
    label: str | None = None
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PatchAliasBody(BaseModel):
    model_config = {"extra": "forbid"}

    label: str | None = None
    description: str | None = None
    metadata: dict[str, Any] | None = None
    status: str | None = None
    visibility: str | None = None


class BindAliasBody(BaseModel):
    model_config = {"extra": "forbid"}

    asset_id: str
    blob_version_id: str | None = None


class AclEntryBody(BaseModel):
    model_config = {"extra": "forbid"}

    principal_kind: str
    principal_id: str
    permission: str = "read"


class ReplaceAclBody(BaseModel):
    model_config = {"extra": "forbid"}

    entries: list[AclEntryBody]


async def _download_response(target, *, proxy: bool = False) -> Response:
    if target.url and not proxy:
        return RedirectResponse(url=target.url, status_code=302)
    assert target.storage_key is not None
    raw = await ensure_file_store().get_bytes(target.storage_key)
    media = target.content_type or "application/octet-stream"
    return Response(content=raw, media_type=media)


@router.get("/assets")
async def list_assets(
    company_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    items = await AssetService(session).list_for_company(
        company_id=company_id,
        principal=principal,
        employee=employee,
        limit=limit,
    )
    return {"items": items}


@router.post("/assets")
async def create_asset(
    body: CreateAssetBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await AssetService(session).create(
        principal=principal,
        employee=employee,
        owner_company_id=body.owner_company_id,
        visibility=body.visibility,
        mime=body.mime,
        title=body.title,
        tags=body.tags,
    )


@router.get("/assets/{asset_id}")
async def get_asset(
    asset_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await AssetService(session).get(
        asset_id=asset_id, principal=principal, employee=employee
    )


@router.patch("/assets/{asset_id}")
async def patch_asset(
    asset_id: str,
    body: PatchAssetBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await AssetService(session).patch(
        asset_id=asset_id,
        principal=principal,
        employee=employee,
        title=body.title,
        tags=body.tags,
        visibility=body.visibility,
    )


@router.delete("/assets/{asset_id}")
async def delete_asset(
    asset_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await AssetService(session).delete(
        asset_id=asset_id, principal=principal, employee=employee
    )


@router.post("/assets/{asset_id}/versions")
async def begin_asset_version(
    asset_id: str,
    body: BeginVersionBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await AssetService(session).begin_version_upload(
        asset_id=asset_id,
        principal=principal,
        employee=employee,
        mime=body.mime,
        ttl_seconds=body.ttl_seconds,
    )


@router.post("/assets/{asset_id}/versions/{version_id}/finalize")
async def finalize_asset_version(
    asset_id: str,
    version_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await AssetService(session).finalize_version(
        asset_id=asset_id,
        version_id=version_id,
        principal=principal,
        employee=employee,
    )


@router.get("/assets/{asset_id}/download")
async def download_asset(
    asset_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
    blob_version_id: str | None = Query(default=None),
    proxy: bool = Query(default=False),
) -> Response:
    target = await DownloadService(session).presign_asset(
        asset_id=asset_id,
        principal=principal,
        employee=employee,
        blob_version_id=blob_version_id,
    )
    # ``proxy=1``: stream the bytes through the API instead of the 302 to the
    # presigned blob URL. Desktop/mobile clients outside the cluster cannot
    # resolve the in-cluster object-store DNS (dev: prodavan-minio:9000), so
    # module-export downloads (budget xlsx / КП PDF) use this mode.
    return await _download_response(target, proxy=proxy)


@router.get("/aliases")
async def list_aliases(
    company_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    items = await AliasService(session).list_for_company(
        company_id=company_id,
        principal=principal,
        employee=employee,
        limit=limit,
    )
    return {"items": items}


@router.post("/aliases")
async def create_alias(
    body: CreateAliasBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await AliasService(session).create(
        slug=body.slug,
        principal=principal,
        employee=employee,
        owner_company_id=body.owner_company_id,
        visibility=body.visibility,
        label=body.label,
        description=body.description,
        metadata=body.metadata,
    )


@router.get("/aliases/{alias_id_or_slug}")
async def get_alias(
    alias_id_or_slug: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await AliasService(session).get_by_id_or_slug(
        alias_id_or_slug=alias_id_or_slug,
        principal=principal,
        employee=employee,
    )


@router.patch("/aliases/{alias_id}")
async def patch_alias(
    alias_id: str,
    body: PatchAliasBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await AliasService(session).patch(
        alias_id=alias_id,
        principal=principal,
        employee=employee,
        label=body.label,
        description=body.description,
        metadata=body.metadata,
        status=body.status,
        visibility=body.visibility,
    )


@router.delete("/aliases/{alias_id}")
async def delete_alias(
    alias_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await AliasService(session).delete(
        alias_id=alias_id, principal=principal, employee=employee
    )


@router.post("/aliases/{alias_id}/bind")
async def bind_alias(
    alias_id: str,
    body: BindAliasBody,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await AliasBindingService(session).bind(
        alias_id=alias_id,
        asset_id=body.asset_id,
        blob_version_id=body.blob_version_id,
        principal=principal,
        employee=employee,
    )


@router.delete("/aliases/{alias_id}/bind")
async def unbind_alias(
    alias_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    return await AliasBindingService(session).unbind(
        alias_id=alias_id, principal=principal, employee=employee
    )


@router.get("/aliases/{alias_id}/bindings")
async def alias_bindings(
    alias_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    alias = await AliasService(session).get(
        alias_id=alias_id, principal=principal, employee=employee
    )
    items = await AliasBindingService(session).history(alias_id, limit=limit)
    return {"alias": alias, "items": items}


@router.get("/aliases/{slug}/resolve")
async def resolve_alias(
    slug: str,
    principal: PrincipalDep,
    session: SessionDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> Response:
    target = await DownloadService(session).resolve_alias_slug(
        slug=slug, principal=principal, employee=employee
    )
    return await _download_response(target)


@router.get("/assets/{asset_id}/acl")
async def get_asset_acl(
    asset_id: str,
    session: SessionDep,
    principal: PrincipalDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    entries = await AccessPolicyService(session).list_acl(
        resource_kind=ContentResourceKind.ASSET,
        resource_id=asset_id,
        principal=principal,
        employee=employee,
    )
    return {"entries": entries}


@router.put("/assets/{asset_id}/acl")
async def put_asset_acl(
    asset_id: str,
    body: ReplaceAclBody,
    session: SessionDep,
    principal: PrincipalDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    entries = await AccessPolicyService(session).replace_acl(
        resource_kind=ContentResourceKind.ASSET,
        resource_id=asset_id,
        entries=[e.model_dump() for e in body.entries],
        principal=principal,
        employee=employee,
    )
    return {"entries": entries}


@router.get("/aliases/{alias_id}/acl")
async def get_alias_acl(
    alias_id: str,
    session: SessionDep,
    principal: PrincipalDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    entries = await AccessPolicyService(session).list_acl(
        resource_kind=ContentResourceKind.ALIAS,
        resource_id=alias_id,
        principal=principal,
        employee=employee,
    )
    return {"entries": entries}


@router.put("/aliases/{alias_id}/acl")
async def put_alias_acl(
    alias_id: str,
    body: ReplaceAclBody,
    session: SessionDep,
    principal: PrincipalDep,
    employee: Annotated[EmployeeRow | None, Depends(get_current_employee)] = None,
) -> dict:
    entries = await AccessPolicyService(session).replace_acl(
        resource_kind=ContentResourceKind.ALIAS,
        resource_id=alias_id,
        entries=[e.model_dump() for e in body.entries],
        principal=principal,
        employee=employee,
    )
    return {"entries": entries}
