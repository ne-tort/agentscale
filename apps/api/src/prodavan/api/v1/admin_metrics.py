"""Platform Admin metrics HTTP (L04 / L09)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.application.admin.company_service import AdminCompanyService

router = APIRouter(prefix="/admin/metrics", tags=["admin-metrics"])


@router.get("/companies")
async def list_companies_metrics(_: PlatformAdminDep, session: SessionDep) -> dict:
    svc = AdminCompanyService(session)
    items = await svc.list_companies_metrics()
    cascade_pending = await svc.list_cascade_pending()
    return {"items": items, "cascade_pending": cascade_pending}


@router.get("/series")
async def metrics_series(
    _: PlatformAdminDep,
    session: SessionDep,
    metric: str = Query(..., min_length=1),
    entity_type: str = Query(..., min_length=1),
    entity_id: str = Query(..., min_length=1),
    window: str = Query("7d"),
) -> dict:
    from prodavan.application.metrics.query import MetricsQuery

    return await MetricsQuery(session).counter_series(
        metric=metric,
        entity_type=entity_type,
        entity_id=entity_id,
        window=window,
    )


@router.post("/rebuild")
async def rebuild_metrics_counters(_: PlatformAdminDep, session: SessionDep) -> dict:
    """One-shot backfill of Redis counters from PG + object store (no text_delta)."""
    from prodavan.application.metrics.backfill import MetricsBackfillService

    stats = await MetricsBackfillService(session).rebuild_all(storage=True)
    await session.commit()
    return {"ok": True, **stats}
