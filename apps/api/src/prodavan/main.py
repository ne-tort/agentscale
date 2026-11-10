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
    session_factory = None
    if settings.plugin_platform_enabled:
        from prodavan.application.plugins.jobs.declarations import (
            install_api_plugin_platform,
        )

        # The full enabled composition is shared with main_plugin_gateway:
        # declarations/stores/tasks, the real lifespan worker hook, platform
        # installation and scoped job-handler binding cannot diverge by process.
        plugin_jobs = install_api_plugin_platform(
            app,
            settings,
            lifespan_manager=lifespan_manager,
        )
        session_factory = plugin_jobs.session_factory
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
            from prodavan.api.v1 import plugin_mcp

            # Authenticated per-server MCP JSON-RPC precedes capability catch-all.
            app.include_router(plugin_mcp.router)
            app.include_router(plugin_capabilities.router)

    # Managed source provisioning and trusted CI publication have separate
    # opt-in flags and never inherit PLUGIN_PLATFORM_ENABLED. Source can be
    # composed alone; CI additionally requires the managed source prerequisites.
    # Both receive the same real GitHubAppClient/configuration, owned solely by
    # the source lifespan resource. CI/GHCR borrow it; neither path enables
    # runtime/install/deploy services.
    from prodavan.api.internal import plugin_artifacts
    from prodavan.application.plugins.wiring.install import (
        install_plugin_ci_publication,
        plugin_ci_publication_enabled,
    )
    from prodavan.application.plugins.wiring.module_sources import (
        build_plugin_ci_approval_reader,
        install_module_source_composition,
        module_source_enabled,
    )

    ci_enabled = plugin_ci_publication_enabled()
    source_composition = None
    if module_source_enabled(settings) or ci_enabled:
        if session_factory is None:
            from prodavan.infrastructure.persistence.database import get_session_factory

            session_factory = get_session_factory()
        source_composition = install_module_source_composition(
            app,
            settings,
            lifespan_manager=lifespan_manager,
        )

    if ci_enabled:
        # This import/constructor is deliberately reached only for enabled CI.
        # Source-only composition remains usable while the approval repository
        # is absent; no ACTIVE grant reader is used as a compatibility fallback.
        assert source_composition is not None
        approval_reader = build_plugin_ci_approval_reader(session_factory)
        install_plugin_ci_publication(
            app,
            settings,
            session_factory=session_factory,
            lifespan_manager=lifespan_manager,
            managed_organization=source_composition.managed_organization,
            github_app=source_composition.github_app,
            source_config=source_composition.config,
            approval_reader=approval_reader,
        )
    app.include_router(plugin_artifacts.router)
    return app


app = create_app()
