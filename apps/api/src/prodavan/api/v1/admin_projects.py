"""Admin project maintenance hooks (P0 / C-MATERIALIZE)."""

from __future__ import annotations

from fastapi import APIRouter

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.api.rate_limit import enforce_rate_limit
from prodavan.application.projects.container_ref_backfill import backfill_object_ws_container_refs
from prodavan.application.projects.sandbox_k8s import run_pvc_probe_job, sandbox_k8s_status
from prodavan.config.settings import settings
from prodavan.core.infra.cache import cache_key

router = APIRouter(prefix="/admin/projects", tags=["admin-projects"])


@router.post("/container-refs/backfill-object-ws")
async def backfill_object_ws_refs(
    _: PlatformAdminDep,
    session: SessionDep,
) -> dict:
    """Rewrite remaining ``local-ws:`` container_ref values to ``object-ws:``."""
    await enforce_rate_limit(
        cache_key("rl", "admin", "container-ref-backfill"),
        limit=int(settings.admin_ops_rate_limit_per_minute or 0),
        detail="admin container-ref backfill rate limit exceeded",
    )
    return await backfill_object_ws_container_refs(session)


@router.get("/sandbox-k8s")
async def sandbox_k8s_status_endpoint(_: PlatformAdminDep) -> dict:
    """Inspect I8 Job wiring. Does not spawn; project create stays object-ws."""
    return sandbox_k8s_status()


@router.post("/sandbox-k8s/pvc-probe")
async def sandbox_k8s_pvc_probe_endpoint(_: PlatformAdminDep) -> dict:
    """Create the same PVC-mount Job as verify_sandbox_job.sh (flag-gated)."""
    await enforce_rate_limit(
        cache_key("rl", "admin", "sandbox-k8s-probe"),
        limit=int(settings.admin_ops_rate_limit_per_minute or 0),
        detail="admin sandbox k8s probe rate limit exceeded",
    )
    return await run_pvc_probe_job()
