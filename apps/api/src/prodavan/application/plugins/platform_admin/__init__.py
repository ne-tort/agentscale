"""Адаптеры PlatformAdmin для Admin API плагинов (задача 12 / F11).

Собственный каталог миграции: не редактирует ``admin/ports.py``,
``api/v1/plugin_admin.py``, ``api/deps.py``, ``infrastructure/auth/jwt.py``,
``wiring/state.py`` и ``docs/**``. Родитель сам импортирует отсюда
``BearerPlatformAdminAdapter`` (кладёт в ``PluginAdminPorts.platform_admin``) и
вызывает ``attach_admin_runtime`` из ``main.py``.

Публичный контракт:

* ``BearerPlatformAdminAdapter`` — реализация ``PlatformAdminPort`` из
  ``application/plugins/admin/ports.py``. Верификация Bearer через существующий
  ``get_jwt_validator().validate`` (JWKS/RS256, ``infrastructure/auth/jwt.py``),
  проверка роли ``platform.admin`` как в ``api/deps.py::require_platform_admin``.
  Fail-closed: нет токена / невалидный / нет роли → 401/403, не «пустой успех».
* ``attach_admin_runtime(app, *, ports, service)`` — кладёт в ``app.state``
  ровно ``plugin_admin_ports`` и ``plugin_admin_service`` (ключи ждёт роутер).
* ``require_admin_runtime(request)`` — FastAPI-зависимость, 503 если не привязано.
* ``Principal`` / ``PlatformAdminContext`` — frozen доменные типы без
  Pydantic/FastAPI-зависимостей.

Пример сборки (делает РОДИТЕЛЬ в ``main.py``, здесь только для справки)::

    from prodavan.application.plugins.admin.ports import PluginAdminPorts
    from prodavan.application.plugins.platform_admin import (
        BearerPlatformAdminAdapter,
        attach_admin_runtime,
    )

    ports = PluginAdminPorts(
        ...,
        platform_admin=BearerPlatformAdminAdapter(),
    )
    service = PluginAdminService(ports)
    attach_admin_runtime(app, ports=ports, service=service)
"""

from .adapter import BearerPlatformAdminAdapter, TokenValidatorPort
from .ports import (
    BEARER_SCHEME,
    PLATFORM_ADMIN_ROLE,
    PlatformAdminContext,
    Principal,
)
from .state import (
    ADMIN_RUNTIME_UNAVAILABLE_CODE,
    STATE_KEYS,
    AppStateLike,
    PluginAdminRuntimeNotAttached,
    RequestLike,
    attach_admin_runtime,
    detach_admin_runtime,
    require_admin_runtime,
)

__all__ = [
    "ADMIN_RUNTIME_UNAVAILABLE_CODE",
    "BEARER_SCHEME",
    "PLATFORM_ADMIN_ROLE",
    "STATE_KEYS",
    "AppStateLike",
    "BearerPlatformAdminAdapter",
    "PlatformAdminContext",
    "PluginAdminRuntimeNotAttached",
    "Principal",
    "RequestLike",
    "TokenValidatorPort",
    "attach_admin_runtime",
    "detach_admin_runtime",
    "require_admin_runtime",
]
