"""Stub marker for smoke / agents."""

from fastapi import APIRouter

router = APIRouter(tags=["stub"])


@router.get("/stub")
async def stub_info() -> dict[str, str]:
    return {
        "status": "stub",
        "message": "Prodavan platform stub. Implement from docs/target/.",
    }
