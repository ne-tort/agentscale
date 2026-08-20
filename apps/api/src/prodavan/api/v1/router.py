"""API v1 router aggregation."""

from fastapi import APIRouter

from prodavan.api.v1 import health

router = APIRouter()
router.include_router(health.router)
