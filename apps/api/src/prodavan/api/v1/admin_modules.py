"""Admin modules — CRUD + meta + bindings + platform-instance data."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel, Field

from prodavan.api.deps import PlatformAdminDep, PrincipalDep, SessionDep
from prodavan.application.content.module_upload_service import ModuleContentUploadService
from prodavan.application.modules.module_instance_service import (
    OWNER_PLATFORM,
    PLATFORM_OWNER_ID,
)
from prodavan.application.modules.module_meta_service import ModuleMetaDocumentService
from prodavan.application.modules.module_service import ModuleService
from prodavan.application.modules.owner_module_data_service import OwnerModuleDataService

router = APIRouter(prefix="/admin/modules", tags=["admin-modules"])


class CreateAdminModuleBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=200)


class PatchAdminModuleBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = Field(default=None, min_length=1, max_length=200)
    company_ids: list[str] | None = None
    cabinet_ids: list[str] | None = None


class MetaDocumentBody(BaseModel):
    model_config = {"extra": "forbid"}

    body: Any


class CopyAdminModuleBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = Field(default=None, min_length=1, max_length=200)


class BindProjectBody(BaseModel):
    model_config = {"extra": "forbid"}

    bind_kind: str = Field(default="local", pattern="^(local|global)$")
    child_may_edit: bool | None = None


class DataRowBody(BaseModel):
    model_config = {"extra": "forbid"}

    body: dict[str, Any]


@router.get("")
async def list_modules(_: PlatformAdminDep, session: SessionDep) -> dict:
    items = await ModuleService(session).list_all_admin()
    return {"items": items}


@router.post("")
async def create_module(
    body: CreateAdminModuleBody,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await ModuleService(session).create_for_admin(name=body.name)


@router.get("/{module_id}")
async def get_module(module_id: str, _: PlatformAdminDep, session: SessionDep) -> dict:
    return await ModuleService(session).get_admin(module_id=module_id)


@router.patch("/{module_id}")
async def patch_module(
    module_id: str,
    body: PatchAdminModuleBody,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await ModuleService(session).update_admin(
        module_id=module_id,
        name=body.name,
        company_ids=body.company_ids,
        cabinet_ids=body.cabinet_ids,
    )


@router.delete("/{module_id}")
async def delete_module(module_id: str, _: PlatformAdminDep, session: SessionDep) -> dict:
    return await ModuleService(session).delete_admin(module_id=module_id)


@router.post("/{module_id}/copy", status_code=201)
async def copy_module(
    module_id: str,
    body: CopyAdminModuleBody,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await ModuleService(session).copy_admin(module_id=module_id, name=body.name)


@router.get("/{module_id}/meta/documents")
async def list_meta_documents(module_id: str, _: PlatformAdminDep, session: SessionDep) -> dict:
    items = await ModuleMetaDocumentService(session).list_documents(module_id=module_id)
    return {"items": items}


@router.get("/{module_id}/meta/documents/{slug}")
async def get_meta_document(
    module_id: str,
    slug: str,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await ModuleMetaDocumentService(session).get_document(module_id=module_id, slug=slug)


@router.put("/{module_id}/meta/documents/{slug}")
async def put_meta_document(
    module_id: str,
    slug: str,
    body: MetaDocumentBody,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await ModuleMetaDocumentService(session).put_document(
        module_id=module_id,
        slug=slug,
        body=body.body,
    )


@router.delete("/{module_id}/meta/documents/{slug}")
async def delete_meta_document(
    module_id: str,
    slug: str,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    await ModuleMetaDocumentService(session).delete_document(module_id=module_id, slug=slug)
    return {"deleted": True}


@router.get("/{module_id}/projects")
async def list_module_project_bindings(
    module_id: str,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    from prodavan.application.modules.module_binding_service import ModuleBindingService

    await ModuleService(session)._get_row(module_id)
    items = await ModuleBindingService(session).list_project_bindings_for_module(module_id)
    return {"items": items}


@router.post("/{module_id}/projects/{project_id}")
async def bind_project(
    module_id: str,
    project_id: str,
    _: PlatformAdminDep,
    session: SessionDep,
    body: BindProjectBody | None = None,
) -> dict:
    payload = body or BindProjectBody()
    return await ModuleService(session).bind_project(
        module_id=module_id,
        project_id=project_id,
        bind_kind=payload.bind_kind,
        child_may_edit=payload.child_may_edit,
    )


@router.delete("/{module_id}/projects/{project_id}")
async def revoke_project(
    module_id: str,
    project_id: str,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await ModuleService(session).revoke_project(module_id=module_id, project_id=project_id)


@router.post("/{module_id}/content/upload")
async def upload_platform_module_content(
    module_id: str,
    principal: PrincipalDep,
    _: PlatformAdminDep,
    session: SessionDep,
    file: UploadFile = File(...),
) -> dict:
    data = await file.read()
    return await ModuleContentUploadService(session).upload_for_platform_module(
        module_id=module_id,
        data=data,
        filename=file.filename or "upload.bin",
        mime=file.content_type,
        principal=principal,
    )


@router.get("/{module_id}/data/{table_slug}")
async def list_platform_module_data(
    module_id: str,
    table_slug: str,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    items = await OwnerModuleDataService(session).list_data_rows(
        owner_kind=OWNER_PLATFORM,
        owner_id=PLATFORM_OWNER_ID,
        module_id=module_id,
        table_slug=table_slug,
    )
    return {"items": items}


@router.post("/{module_id}/data/{table_slug}")
async def create_platform_module_data(
    module_id: str,
    table_slug: str,
    body: DataRowBody,
    principal: PrincipalDep,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await OwnerModuleDataService(session).create_data_row(
        owner_kind=OWNER_PLATFORM,
        owner_id=PLATFORM_OWNER_ID,
        module_id=module_id,
        table_slug=table_slug,
        body=body.body,
        created_by=principal.sub,
    )


@router.patch("/{module_id}/data/{table_slug}/{row_id}")
async def update_platform_module_data(
    module_id: str,
    table_slug: str,
    row_id: str,
    body: DataRowBody,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await OwnerModuleDataService(session).update_data_row(
        owner_kind=OWNER_PLATFORM,
        owner_id=PLATFORM_OWNER_ID,
        module_id=module_id,
        table_slug=table_slug,
        row_id=row_id,
        body=body.body,
    )


@router.delete("/{module_id}/data/{table_slug}/{row_id}")
async def delete_platform_module_data(
    module_id: str,
    table_slug: str,
    row_id: str,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await OwnerModuleDataService(session).delete_data_row(
        owner_kind=OWNER_PLATFORM,
        owner_id=PLATFORM_OWNER_ID,
        module_id=module_id,
        table_slug=table_slug,
        row_id=row_id,
    )
