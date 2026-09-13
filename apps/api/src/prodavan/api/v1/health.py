"""Health / probe endpoints for k3s + versioned C-API-HEALTH."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

from prodavan.config.settings import settings
from prodavan.core.infra.redis_manager import get_redis_manager
from prodavan.core.wiring import get_lifespan_manager
from prodavan.infrastructure.persistence.database import get_engine

router = APIRouter(tags=["ops"])


def _build_meta() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "build": settings.build_id,
    }


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """Liveness-ish smoke + build metadata (docs / clients)."""
    return {"status": "ok", **_build_meta()}


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    """k3s livenessProbe — process up (minimal body)."""
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness(request: Request) -> dict[str, Any]:
    """k3s readinessProbe — DB + required infra managers."""
    checks: dict[str, str] = {}

    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "NOT_READY",
                "message": f"database: {str(exc)[:200]}",
                "checks": {**checks, "database": "fail"},
            },
        ) from exc

    redis_mgr = get_redis_manager()
    redis_needed = settings.redis_required or (redis_mgr is not None and redis_mgr.enabled)
    if redis_needed:
        ok = redis_mgr is not None and await redis_mgr.ping()
        if not ok:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "NOT_READY",
                    "message": "redis: unavailable",
                    "checks": {**checks, "redis": "fail"},
                },
            )
        checks["redis"] = "ok"
    elif redis_mgr is not None and redis_mgr.enabled is False:
        checks["redis"] = "disabled"

    lifespan = getattr(request.app.state, "lifespan_manager", None) or get_lifespan_manager()
    extras: dict[str, Any] = {}
    if lifespan is not None:
        report = await lifespan.health_report()
        extras["resources"] = {
            k: ("ok" if v is True else "fail" if v is False else "n/a") for k, v in report.items()
        }
        required_resources: list[tuple[str, bool]] = [
            ("kafka", settings.kafka_required),
            ("file_store", settings.object_store_required),
            ("worker", settings.celery_required),
            ("k8s", settings.pod_k8s_required),
            ("mongodb", settings.mongodb_required),
            ("opensearch", settings.opensearch_required),
        ]
        for name, required in required_resources:
            if not required:
                continue
            status = report.get(name)
            if status is not True:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "NOT_READY",
                        "message": f"{name}: unavailable",
                        "checks": {**checks, name: "fail"},
                        **extras,
                    },
                )
            checks[name] = "ok"

    if settings.pod_k8s_required:
        from prodavan.core.infra.k8s_manager import get_k8s_manager

        k8s_mgr = get_k8s_manager()
        if k8s_mgr is None or k8s_mgr.client is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "NOT_READY",
                    "message": "metrics_server: k8s client unavailable",
                    "checks": {**checks, "metrics_server": "fail"},
                    **extras,
                },
            )
        metrics_ok = await k8s_mgr.client.probe_metrics_server()
        if not metrics_ok:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "NOT_READY",
                    "message": "metrics_server: unavailable",
                    "checks": {**checks, "metrics_server": "fail"},
                    **extras,
                },
            )
        checks["metrics_server"] = "ok"

    return {"status": "ok", **_build_meta(), "checks": checks, **extras}
