"""FastAPI application factory — L00 platform skeleton + P0 core lifespan.

Product behavior: docs/target/. Do not restore legacy domain from git history.
"""

import logging

from fastapi import FastAPI

from prodavan.api.exception_handlers import register_exception_handlers
from prodavan.api.v1 import health as health_routes
from prodavan.api.v1 import plugin_gateway
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
    # Plugin Gateway (дизайн v4 §7.1): /webhook/{plugin}/{company}/* и
    # /pluginpage/{plugin}/{company}/* живут В КОРНЕ хоста, без /api/v1 —
    # их ведёт отдельный Ingress plugins-gateway с router.priority 120.
    # Роутер /capabilities/v1/* также монтируется без префикса: allowlist
    # middleware задачи 02 разрешает только ^/capabilities/v1/, и любой
    # внешний префикс дал бы 403 на всех эндпоинтах.
    # До вызова install_plugin_gateway_runtime() роутер отвечает 503 (fail-closed).
    # Внимание: GET /health этого роутера совпадает с health_routes.router выше;
    # k8s-проба использует корневую /health, зарегистрированную ПЕРВОЙ.
    if settings.plugin_platform_enabled:
        from prodavan.application.plugins.transport.guard import register_plugin_surface_guard

        # Доверенный контур плагинов: guard ДО роутинга, иначе /agent/* с
        # plugin-токеном дал бы 404 вместо 403 (критерий §11.17).
        register_plugin_surface_guard(app)
        app.include_router(plugin_gateway.router)
        try:
            from prodavan.api.v1 import plugin_capabilities
        except ModuleNotFoundError as exc:
            # Fail-closed: capability-роутер ещё не собран. Различаем «нет
            # модуля» (ожидаемо на промежуточном коммите) и «модуль есть, но
            # его собственный импорт упал» — второе маскировать нельзя.
            if exc.name != "prodavan.api.v1.plugin_capabilities":
                raise
            logger.error("plugin platform enabled but /capabilities/v1 router is unavailable")
        else:
            app.include_router(plugin_capabilities.router)
    return app


app = create_app()
