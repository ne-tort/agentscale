"""API v1 router."""

from fastapi import APIRouter

from prodavan.api.v1 import admin_companies, admin_metrics, agent, ai_keys, cabinets, health, identity, projects, stub

router = APIRouter()
router.include_router(health.router)
router.include_router(stub.router)
router.include_router(identity.router)
router.include_router(admin_companies.router)
router.include_router(admin_metrics.router)
router.include_router(admin_companies.company_router)
router.include_router(ai_keys.router)
router.include_router(cabinets.router)
router.include_router(projects.cabinet_projects_router)
router.include_router(projects.router)
router.include_router(agent.router)
