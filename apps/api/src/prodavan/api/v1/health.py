"""Health / probe endpoints for k3s."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from prodavan.infrastructure.persistence.database import get_engine

router = APIRouter(tags=["ops"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Lightweight check (no DB) — keep for docs / smoke."""
    return {"status": "ok"}


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    """k3s livenessProbe — process up."""
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness() -> dict[str, str]:
    """k3s readinessProbe — DB reachable."""
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"status": "not_ready", "reason": "database", "error": str(exc)[:200]},
        ) from exc
    return {"status": "ok"}
