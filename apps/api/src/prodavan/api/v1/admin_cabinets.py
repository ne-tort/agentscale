"""Admin cabinets — CRUD + orphan schema GC."""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.api.rate_limit import enforce_rate_limit
from prodavan.application.cabinets.instance_service import CabinetInstanceService
from prodavan.application.cabinets.schema_gc import gc_orphan_cabinet_schemas
from prodavan.config.settings import settings
from prodavan.core.infra.cache import cache_key

router = APIRouter(prefix="/admin/cabinets", tags=["admin-cabinets"])


class CreateAdminCabinetBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=200)
    company_id: str | None = Field(default=None, min_length=3, max_length=40)
    company_ids: list[str] = Field(default_factory=list)


class PatchAdminCabinetBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = Field(default=None, min_length=1, max_length=200)
    company_id: str | None = Field(default=None, min_length=3, max_length=40)
    company_ids: list[str] | None = None
    module_ids: list[str] | None = None
    company_grant_scope: str | None = Field(default=None, pattern="^(selected|all)$")


@router.get("")
async def list_cabinets(_: PlatformAdminDep, session: SessionDep) -> dict:
    items = await CabinetInstanceService(session).list_all_admin()
    return {"items": items}


@router.post("")
async def create_cabinet(
    body: CreateAdminCabinetBody,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await CabinetInstanceService(session).create_for_admin(
        name=body.name,
        company_id=body.company_id,
        company_ids=body.company_ids,
    )


@router.post("/gc-orphan-schemas")
async def gc_orphan_schemas(
    _: PlatformAdminDep,
    session: SessionDep,
    dry_run: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=200),
    enqueue: bool = Query(default=False),
) -> dict:
    """List or DROP ``cab_inst_*`` schemas without a ``cabinet_instances`` row."""
    await enforce_rate_limit(
        cache_key("rl", "admin", "gc-orphan-schemas"),
        limit=int(settings.admin_ops_rate_limit_per_minute or 0),
        detail="admin orphan-schema GC rate limit exceeded",
    )
    if enqueue:
        from prodavan.core.jobs.enqueue import enqueue_gc_orphan_cabinet_schemas

        return enqueue_gc_orphan_cabinet_schemas(dry_run=dry_run, limit=limit)
    return await gc_orphan_cabinet_schemas(session, dry_run=dry_run, limit=limit)


@router.get("/{cabinet_id}")
async def get_cabinet(cabinet_id: str, _: PlatformAdminDep, session: SessionDep) -> dict:
    return await CabinetInstanceService(session).get_admin(cabinet_id=cabinet_id)


@router.patch("/{cabinet_id}")
async def patch_cabinet(
    cabinet_id: str,
    body: PatchAdminCabinetBody,
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    return await CabinetInstanceService(session).update_admin(
        cabinet_id=cabinet_id,
        name=body.name,
        company_id=body.company_id,
        company_ids=body.company_ids,
        module_ids=body.module_ids,
        company_grant_scope=body.company_grant_scope,
    )


@router.delete("/{cabinet_id}")
async def delete_cabinet(
    cabinet_id: str, admin: PlatformAdminDep, session: SessionDep
) -> dict:
    return await CabinetInstanceService(session).soft_delete(
        cabinet_id=cabinet_id, principal=admin, employee=None
    )


@router.post("/{cabinet_id}/restore")
async def restore_cabinet(
    cabinet_id: str, admin: PlatformAdminDep, session: SessionDep
) -> dict:
    return await CabinetInstanceService(session).restore(
        cabinet_id=cabinet_id, principal=admin, employee=None
    )


@router.delete("/{cabinet_id}/purge")
async def purge_cabinet(
    cabinet_id: str, admin: PlatformAdminDep, session: SessionDep
) -> dict:
    return await CabinetInstanceService(session).delete_with_cascade(cabinet_id=cabinet_id)
