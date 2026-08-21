"""API v1 router — stub surface only."""

from fastapi import APIRouter

from prodavan.api.v1 import health, stub

router = APIRouter()
router.include_router(health.router)
router.include_router(stub.router)
