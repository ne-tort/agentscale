"""API v1 router."""

from fastapi import APIRouter

from prodavan.api.v1 import (
    admin_cabinets,
    admin_catalogs,
    admin_companies,
    admin_containers,
    admin_metrics,
    admin_object_store,
    admin_platform_events,
    admin_projects,
    admin_starter_bundles,
    admin_triggers,
    agent,
    ai_keys,
    auth,
    cabinets,
    health,
    identity,
    projects,
    stub,
)

router = APIRouter()
router.include_router(health.router)
router.include_router(stub.router)
router.include_router(auth.router)
router.include_router(identity.router)
router.include_router(admin_companies.router)
router.include_router(admin_metrics.router)
router.include_router(admin_starter_bundles.admin_router)
router.include_router(admin_starter_bundles.router)
router.include_router(admin_companies.company_router)
router.include_router(admin_triggers.router)
router.include_router(admin_platform_events.router)
router.include_router(admin_projects.router)
router.include_router(admin_containers.router)
router.include_router(admin_cabinets.router)
router.include_router(admin_object_store.router)
router.include_router(admin_catalogs.router)
router.include_router(ai_keys.router)
router.include_router(cabinets.router)
router.include_router(projects.cabinet_projects_router)
router.include_router(projects.router)
router.include_router(agent.router)
