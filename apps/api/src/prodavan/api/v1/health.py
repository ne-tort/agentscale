"""Health / probe endpoints for k3s + versioned C-API-HEALTH."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from prodavan.config.settings import settings
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
async def readiness() -> dict[str, Any]:
    """k3s readinessProbe — DB reachable."""
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "NOT_READY",
                "message": f"database: {str(exc)[:200]}",
            },
        ) from exc
    return {"status": "ok", **_build_meta()}
