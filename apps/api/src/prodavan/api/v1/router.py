"""API v1 router aggregation."""

from fastapi import APIRouter

from prodavan.api.v1 import auth, cabinets, health, me

router = APIRouter()
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(me.router)
router.include_router(cabinets.router)
