"""Always-running Plugin Gateway process (дизайн v4 §7.1/§7.2).

Отдельный uvicorn-процесс на порту 8080, который ведёт ТОЛЬКО plugin-surface:
``/webhook/{plugin}/{company}/*``, ``/pluginpage/{plugin}/{company}/*`` и
``/capabilities/v1/*``. Ingress ``plugins-gateway`` (router.priority 120)
направляет сюда запросы, которые иначе уехали бы в SPA (инцидент #331).

Почему НЕ ``prodavan.main:app``: основной API несёт pod-surface allowlist,
агентский trust и сотни маршрутов. У плагинов ДРУГОЙ trust-контур (ADR-008):
свой guard, свой KeyRing, свои capability-адаптеры. Смешивать их в одном
приложении — ровно та ошибка, которую мы устраняли в v4.

Композиция ОДНА на оба процесса (``install_plugin_platform``), поэтому гранты,
отзывы, ключи и аудит не расходятся между API и шлюзом.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from prodavan.api.exception_handlers import register_exception_handlers
from prodavan.config.settings import settings
from prodavan.core.middleware import register_cors, register_json_charset, register_trace_id
from prodavan.core.trace_context import install_trace_id_log_filter
from prodavan.core.wiring import build_lifespan_manager

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    install_trace_id_log_filter()
    lifespan_manager = build_lifespan_manager()
    app = FastAPI(
        title="Agentscale Plugin Gateway",
        version=settings.app_version,
        lifespan=lifespan_manager.as_fastapi_lifespan(),
    )
    register_trace_id(app)
    register_json_charset(app)
    # Pod-surface allowlist здесь НЕ регистрируется: у плагинов свой guard,
    # который ставит install_plugin_platform() (transport/guard.py).
    register_cors(app, allow_origins=settings.cors_origin_list)
    register_exception_handlers(app)

    # k3s-пробы живые/готовые. Готовность = привязанный runtime платформы;
    # до этого запросы на plugin-surface честно получают 503.
    @app.get("/health/live")
    async def live() -> dict:
        return {"status": "alive"}

    @app.get("/health/ready")
    async def ready() -> dict:
        runtime = getattr(app.state, "plugin_gateway_runtime", None)
        if runtime is None:
            return {"status": "starting"}
        return {"status": "ready"}

    # Роутеры монтируются ВСЕГДА: при выключенной платформе они отдают 503
    # (fail-closed), а не 404/SPA-fallback. Префикса /api/v1 НЕТ: allowlist
    # middleware задачи 02 разрешает только ^/capabilities/v1/, а внешние
    # адреса §7.1 живут в корне хоста.
    from prodavan.api.v1 import plugin_gateway

    app.include_router(plugin_gateway.router)

    if settings.plugin_platform_enabled:
        from prodavan.application.plugins.wiring.install import (
            LazyRedisManager,
            install_plugin_platform,
        )
        from prodavan.infrastructure.persistence.database import get_session_factory

        install_plugin_platform(
            app,
            settings,
            session=get_session_factory(),
            redis=LazyRedisManager(),
        )
        from prodavan.api.v1 import plugin_capabilities

        app.include_router(plugin_capabilities.router)

    return app


app = create_app()
