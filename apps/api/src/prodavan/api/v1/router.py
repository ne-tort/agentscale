"""API v1 router aggregation."""

from fastapi import APIRouter

from prodavan.api.v1 import (
    admin,
    auth,
    cabinet_spi,
    cabinets,
    catalogs,
    health,
    me,
    platform_agent,
    projects,
    prompts,
    specs,
)

router = APIRouter()
# Also mounted at app root for k3s probes (/health/live|/ready) — see main.py
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(me.router)
router.include_router(admin.router)
router.include_router(cabinets.router)
router.include_router(cabinet_spi.router)
router.include_router(projects.router)
router.include_router(prompts.router)
router.include_router(platform_agent.router)
router.include_router(specs.router)
router.include_router(catalogs.router)
