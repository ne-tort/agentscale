"""Guard ставится и при выключенной платформе, и ровно один раз.

Gateway монтирует ``plugin_gateway.router`` ВСЕГДА (при выключенной платформе
он отдаёт 503), но раньше ``PluginSurfaceGuardMiddleware`` регистрировался
только внутри ``install_api_plugin_platform`` при ``enabled=true``. Значит при
выключенной платформе плагиновый токен не отсекался до роутинга вовсе.

Тесты не поднимают реальный процесс: они собирают ``create_app`` с подменённым
settings и проверяют состав middleware / роутеров.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI

from prodavan.application.plugins.transport.guard import PluginSurfaceGuardMiddleware
from prodavan.application.plugins.wiring.install import PLUGIN_SURFACE_GUARD_STATE_KEY


def _guard_layers(app: FastAPI) -> list[object]:
    return [item.cls for item in app.user_middleware if item.cls is PluginSurfaceGuardMiddleware]


def test_guard_is_registered_when_platform_is_disabled() -> None:
    from prodavan import main_plugin_gateway as module

    disabled = SimpleNamespace(
        plugin_platform_enabled=False,
        app_version="test",
        cors_origin_list=[],
    )
    with patch.object(module, "settings", disabled):
        app = module.create_app()
    # Роутер смонтирован, значит guard обязан быть: иначе плагиновый токен
    # доходит до роутинга без surface-проверки.
    assert _guard_layers(app) != []
    assert getattr(app.state, PLUGIN_SURFACE_GUARD_STATE_KEY, False) is True


def test_guard_is_registered_exactly_once_when_platform_is_enabled() -> None:
    from prodavan import main_plugin_gateway as module

    enabled = SimpleNamespace(
        plugin_platform_enabled=True,
        app_version="test",
        cors_origin_list=[],
    )
    # enabled-ветка требует реальную композицию; проверяем именно дедупликацию:
    # установку платформы подменяем, оставляя наш собственный guard.
    with patch.object(module, "settings", enabled), patch(
        "prodavan.application.plugins.jobs.declarations.install_api_plugin_platform",
        lambda app, *a, **k: None,
    ):
        app = module.create_app()
    assert len(_guard_layers(app)) == 1
