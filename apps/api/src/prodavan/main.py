"""FastAPI application factory — L00 platform skeleton + P0 core lifespan.

Product behavior: docs/target/. Do not restore legacy domain from git history.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from prodavan.api.exception_handlers import register_exception_handlers
from prodavan.api.v1 import health as health_routes
from prodavan.api.v1.router import router as v1_router
from prodavan.config.settings import settings
from prodavan.core.wiring import build_lifespan_manager


def create_app() -> FastAPI:
    lifespan_manager = build_lifespan_manager()
    app = FastAPI(
        title="Prodavan API",
        version=settings.app_version,
        lifespan=lifespan_manager.as_fastapi_lifespan(),
    )
    app.state.lifespan_manager = lifespan_manager
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    # k3s probes (no /api/v1 prefix) — docs/07-infrastructure/k3s-services.md
    app.include_router(health_routes.router)
    app.include_router(v1_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
