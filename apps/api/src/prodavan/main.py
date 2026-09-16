"""FastAPI application factory — L00 platform skeleton + P0 core lifespan.

Product behavior: docs/target/. Do not restore legacy domain from git history.
"""

from fastapi import FastAPI

from prodavan.api.exception_handlers import register_exception_handlers
from prodavan.api.v1 import health as health_routes
from prodavan.api.v1.router import router as v1_router
from prodavan.config.settings import settings
from prodavan.core.middleware import register_cors, register_pod_surface_allowlist, register_trace_id
from prodavan.core.wiring import build_lifespan_manager


def create_app() -> FastAPI:
    lifespan_manager = build_lifespan_manager()
    app = FastAPI(
        title="Prodavan API",
        version=settings.app_version,
        lifespan=lifespan_manager.as_fastapi_lifespan(),
    )
    app.state.lifespan_manager = lifespan_manager
    # Trace id first so request.state.trace_id is set for all later middleware
    # and exception handlers (audit XCUT-P2a).
    register_trace_id(app)
    # Allowlist before CORS so pod credential checks always run.
    register_pod_surface_allowlist(app)
    register_cors(app, allow_origins=settings.cors_origin_list)
    register_exception_handlers(app)
    # k3s probes (no /api/v1 prefix) — docs/07-infrastructure/k3s-services.md
    app.include_router(health_routes.router)
    app.include_router(v1_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
