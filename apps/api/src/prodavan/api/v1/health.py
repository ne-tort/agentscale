"""Health check endpoint."""

from fastapi import APIRouter

router = APIRouter(tags=["ops"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
