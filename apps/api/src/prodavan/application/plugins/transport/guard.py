"""Ветка allowlist middleware для plugin-токенов (задача 04, критерий №17).

**Не правка** ``core/middleware.py``: агентская ветка
``PodSurfaceAllowlistMiddleware`` / ``_POD_SURFACE_RE`` остаётся как есть и
обслуживает ``typ=pod_bridge`` на ``:8001``. Здесь — НЕЗАВИСИМАЯ ветка для
``typ=plugin_capability`` на route-group ``/capabilities/v1/*`` Plugin Gateway
(v4 §4.1, §13.3, §14.3).

Зачем нужен этот файл, если у задачи 02 уже есть ``identity/allowlist.py``:
там — stateless функция ``assert_plugin_surface`` и предикаты, а здесь —
готовая к регистрации **HTTP-ветка**, которая вызывает их на КАЖДОМ запросе с
plugin-токеном ДО роутинга. Без неё запрос ``/agent/events`` с plugin-токеном
не попал бы ни в один маршрут ``/capabilities/v1/*`` и получил бы обычный 404
вместо 403 ``FORBIDDEN_SURFACE`` (критерий №17).

Родитель при F11-интеграции может:
  * либо зарегистрировать ``register_plugin_surface_guard(app)`` из этого файла;
  * либо сделать такую же ветку в своём middleware самостоятельно.

Оба варианта не требуют правки ``core/middleware.py`` / ``main_pod.py`` и не
трогают агентский trust. Fail-closed: нет keyring/revocation -> 503; ошибка
verify/ revocation/ surface -> 401/403 с типизированным ``code``.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from prodavan.application.plugins.identity.allowlist import is_plugin_capability_token
from prodavan.application.plugins.transport.deps import (
    BEARER_PREFIX,
    get_plugin_claims,
    resolve_identity_config,
)
from prodavan.application.plugins.transport.errors import (
    PluginTransportError,
    plugin_problem_response,
)


class PluginSurfaceGuardMiddleware(BaseHTTPMiddleware):
    """Отклонить plugin-токен вне ``/capabilities/v1/*`` до роутинга.

    Ветвление по ``typ`` (peek БЕЗ проверки подписи) — ровно как
    ``middleware._is_pod_bridge_token`` у агентов, но для своего контура.
    Авторизацию делает ``get_plugin_claims`` (verify + revocation +
    ``assert_plugin_surface``), а не «доверие Gateway по умолчанию» (§7.2).
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        header = request.headers.get("Authorization") or ""
        if header.startswith(BEARER_PREFIX):
            token = header[len(BEARER_PREFIX) :].strip()
            if token and is_plugin_capability_token(token):
                try:
                    # Both dependencies are async def: without await they return
                    # unexecuted coroutines, so verification/surface checks never
                    # ran and the guard failed open (200/404 instead of 403).
                    config = await resolve_identity_config(request)
                    claims = await get_plugin_claims(request, config)
                    # Положить проверенные claims в state: роутер всё равно
                    # перепроверит через свою зависимость, но родительские
                    # middleware/логи могут читать доверенную identity здесь.
                    request.state.plugin_claims = claims
                except PluginTransportError as exc:
                    return plugin_problem_response(exc.problem, request)
        return await call_next(request)


def register_plugin_surface_guard(app: FastAPI) -> None:
    """Зарегистрировать plugin-ветку allowlist (вызывает родитель при F11).

    Порядок относительно CORS/TraceId не критичен, но guard должен быть
    ДО роутинга и не подменять агентскую ветку ``register_pod_surface_allowlist``.
    """

    app.add_middleware(PluginSurfaceGuardMiddleware)


# Совместимый алиас на случай, если родитель захочет вернуть response напрямую.
def plugin_problem(exc: PluginTransportError, request: Request) -> JSONResponse:
    return plugin_problem_response(exc.problem, request)


__all__ = [
    "PluginSurfaceGuardMiddleware",
    "plugin_problem",
    "register_plugin_surface_guard",
]
