"""FastAPI application factory — platform STUB.

Product behavior must be implemented from docs/target/, not legacy code.
"""

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
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Prodavan API (stub)",
        version="0.0.0-stub",
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
    # k3s probes (no /api/v1 prefix) — docs/07-infrastructure/k3s-services.md
    app.include_router(health_routes.router)
    app.include_router(v1_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
