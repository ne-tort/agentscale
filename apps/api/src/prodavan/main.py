"""FastAPI application factory — L00 platform skeleton + P0 core lifespan.

Product behavior: docs/target/. Do not restore legacy domain from git history.
"""

import logging

from fastapi import FastAPI

from prodavan.api.exception_handlers import register_exception_handlers
from prodavan.api.v1 import health as health_routes
from prodavan.api.v1.router import router as v1_router
from prodavan.config.settings import settings
from prodavan.core.middleware import (
    register_cors,
    register_json_charset,
    register_pod_surface_allowlist,
    register_trace_id,
)
from prodavan.core.trace_context import install_trace_id_log_filter
from prodavan.core.wiring import build_lifespan_manager

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    # Bind trace_id to the structured log context (audit XCUT-P2a) before the
    # first request is served.
    install_trace_id_log_filter()
    lifespan_manager = build_lifespan_manager()
    app = FastAPI(
        title="Agentscale API",
        version=settings.app_version,
        lifespan=lifespan_manager.as_fastapi_lifespan(),
    )
    app.state.lifespan_manager = lifespan_manager
    # Trace id first so request.state.trace_id is set for all later middleware
    # and exception handlers (audit XCUT-P2a).
    register_trace_id(app)
    # JSON bodies are UTF-8 — declare the charset so every client (Dart http
    # defaults to latin1 without it) decodes them correctly (no «â€"» mojibake
    # in error details).
    register_json_charset(app)
    # Allowlist before CORS so pod credential checks always run.
    register_pod_surface_allowlist(app)
    register_cors(app, allow_origins=settings.cors_origin_list)
    register_exception_handlers(app)
    # k3s probes (no /api/v1 prefix) — docs/07-infrastructure/k3s-services.md
    app.include_router(health_routes.router)
    app.include_router(v1_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
