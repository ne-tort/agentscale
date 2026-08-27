"""Admin modules — CRUD + meta + bindings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.application.modules.module_meta_service import ModuleMetaDocumentService
from prodavan.application.modules.module_service import ModuleService

router = APIRouter(prefix="/admin/modules", tags=["admin-modules"])


class CreateAdminModuleBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=200)


class PatchAdminModuleBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = Field(default=None, min_length=1, max_length=200)
    company_ids: list[str] | None = None


class MetaDocumentBody(BaseModel):
    model_config = {"extra": "forbid"}

    body: Any


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
    )


@router.delete("/{module_id}")
async def delete_module(module_id: str, _: PlatformAdminDep, session: SessionDep) -> dict:
    return await ModuleService(session).delete_admin(module_id=module_id)


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


@router.post("/{module_id}/projects/{project_id}")
async def bind_project(
    module_id: str,
    project_id: str,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await ModuleService(session).bind_project(module_id=module_id, project_id=project_id)


@router.delete("/{module_id}/projects/{project_id}")
async def revoke_project(
    module_id: str,
    project_id: str,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await ModuleService(session).revoke_project(module_id=module_id, project_id=project_id)
