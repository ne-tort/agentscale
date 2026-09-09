"""Tenant Infra HTTP — Cache API for Project Pods."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from prodavan.api.agent_auth import PodBridgeDep
from prodavan.api.deps import SessionDep
from prodavan.application.tenant_infra.service import TenantInfraService

router = APIRouter(tags=["tenant-infra"])


class CacheSetBody(BaseModel):
    value: str
    ttl_sec: int | None = Field(default=None, ge=1, le=86400 * 7)


class CacheIncrBody(BaseModel):
    amount: int = 1


@router.get("/projects/{project_id}/infra/cache/{key:path}")
async def cache_get(
    project_id: str,
    key: str,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await TenantInfraService().get(
        bridge=bridge, project_id=project_id, key=key, session=session
    )


@router.put("/projects/{project_id}/infra/cache/{key:path}")
async def cache_set(
    project_id: str,
    key: str,
    body: CacheSetBody,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await TenantInfraService().set(
        bridge=bridge,
        project_id=project_id,
        key=key,
        value=body.value,
        ttl_sec=body.ttl_sec,
        session=session,
    )


@router.delete("/projects/{project_id}/infra/cache/{key:path}")
async def cache_delete(
    project_id: str,
    key: str,
    bridge: PodBridgeDep,
    session: SessionDep,
) -> dict[str, Any]:
    return await TenantInfraService().delete(
        bridge=bridge, project_id=project_id, key=key, session=session
    )


@router.post("/projects/{project_id}/infra/cache/{key:path}/incr")
async def cache_incr(
    project_id: str,
    key: str,
    bridge: PodBridgeDep,
    session: SessionDep,
    body: CacheIncrBody | None = None,
) -> dict[str, Any]:
    amount = body.amount if body is not None else 1
    return await TenantInfraService().incr(
        bridge=bridge,
        project_id=project_id,
        key=key,
        amount=amount,
        session=session,
    )
