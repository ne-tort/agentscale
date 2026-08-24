"""Admin cabinet maintenance (P0 orphan schema GC)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.api.rate_limit import enforce_rate_limit
from prodavan.application.cabinets.schema_gc import gc_orphan_cabinet_schemas
from prodavan.config.settings import settings
from prodavan.core.infra.cache import cache_key

router = APIRouter(prefix="/admin/cabinets", tags=["admin-cabinets"])


@router.post("/gc-orphan-schemas")
async def gc_orphan_schemas(
    _: PlatformAdminDep,
    session: SessionDep,
    dry_run: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=200),
    enqueue: bool = Query(default=False),
) -> dict:
    """List or DROP ``cab_inst_*`` schemas without a ``cabinet_instances`` row.

    When ``enqueue=true``, schedules Celery task (ignores dry_run for listing —
    task uses the same dry_run/limit kwargs).
    """
    await enforce_rate_limit(
        cache_key("rl", "admin", "gc-orphan-schemas"),
        limit=int(settings.admin_ops_rate_limit_per_minute or 0),
        detail="admin orphan-schema GC rate limit exceeded",
    )
    if enqueue:
        from prodavan.core.jobs.enqueue import enqueue_gc_orphan_cabinet_schemas

        return enqueue_gc_orphan_cabinet_schemas(dry_run=dry_run, limit=limit)
    return await gc_orphan_cabinet_schemas(session, dry_run=dry_run, limit=limit)
