"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from prodavan.api.exception_handlers import register_exception_handlers
from prodavan.api.v1 import health as health_routes
from prodavan.api.v1.router import router as v1_router
from prodavan.config.settings import settings
from prodavan.infrastructure.persistence.database import dispose_engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    from prodavan.application.services.auth_service import ensure_platform_admin
    from prodavan.infrastructure.persistence.database import get_session_factory

    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            await ensure_platform_admin(session)
        except Exception:
            # DB may be unavailable at import-time in some tooling; login seed retries on demand.
            pass
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Prodavan API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    # k3s probes per docs/07-infrastructure/k3s-services.md (no /api/v1 prefix)
    app.include_router(health_routes.router)
    app.include_router(v1_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
