"""Pod-only FastAPI surface — listens on :8001 (sandbox NetworkPolicy target).

Mounts only routes Project Pods may call. Not exposed via Traefik Ingress.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from prodavan.api.exception_handlers import register_exception_handlers
from prodavan.api.internal import pods as internal_pods
from prodavan.api.v1 import agent, health as health_routes, pod_modules, tenant_infra
from prodavan.config.settings import settings
from prodavan.core.middleware import register_cors, register_pod_surface_allowlist
from prodavan.core.wiring import build_pod_surface_lifespan_manager


def create_pod_app() -> FastAPI:
    lifespan_manager = build_pod_surface_lifespan_manager()
    app = FastAPI(
        title="Prodavan Pod API",
        version=settings.app_version,
        lifespan=lifespan_manager.as_fastapi_lifespan(),
    )
    app.state.lifespan_manager = lifespan_manager
    register_pod_surface_allowlist(app)
    register_cors(app, allow_origins=settings.cors_origin_list)
    register_exception_handlers(app)
    app.include_router(health_routes.router)

    v1 = APIRouter()
    # Agent router includes employee routes; Bridge JWT cannot satisfy PrincipalDep,
    # and allowlist + Bridge project bind confine pod credentials.
    v1.include_router(agent.router)
    v1.include_router(tenant_infra.router)
    v1.include_router(pod_modules.router)
    v1.include_router(internal_pods.router)
    app.include_router(v1, prefix=settings.api_v1_prefix)
    return app


app = create_pod_app()
