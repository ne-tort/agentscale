"""Admin object-store maintenance (P0 orphan blob GC)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.api.rate_limit import enforce_rate_limit
from prodavan.application.infra.blob_gc import gc_orphan_blobs
from prodavan.config.settings import settings
from prodavan.core.infra.cache import cache_key

router = APIRouter(prefix="/admin/object-store", tags=["admin-object-store"])


@router.post("/gc-orphan-blobs")
async def gc_orphan_blobs_endpoint(
    _: PlatformAdminDep,
    session: SessionDep,
    dry_run: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=200),
    scan_limit: int = Query(default=500, ge=1, le=5000),
    enqueue: bool = Query(default=False),
) -> dict:
    """Inventory or wipe orphan ``cabinet_packages/`` and ``projects/`` prefixes."""
    await enforce_rate_limit(
        cache_key("rl", "admin", "gc-orphan-blobs"),
        limit=int(settings.admin_ops_rate_limit_per_minute or 0),
        detail="admin orphan-blob GC rate limit exceeded",
    )
    if enqueue:
        from prodavan.core.jobs.enqueue import enqueue_gc_orphan_blobs

        return enqueue_gc_orphan_blobs(dry_run=dry_run, limit=limit, scan_limit=scan_limit)
    return await gc_orphan_blobs(
        session, dry_run=dry_run, limit=limit, scan_limit=scan_limit
    )
