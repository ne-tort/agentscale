"""API v1 router aggregation."""

from fastapi import APIRouter

from prodavan.api.v1 import auth, cabinets, catalogs, health, me, projects, prompts, specs

router = APIRouter()
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(me.router)
router.include_router(cabinets.router)
router.include_router(projects.router)
router.include_router(prompts.router)
router.include_router(specs.router)
router.include_router(catalogs.router)
