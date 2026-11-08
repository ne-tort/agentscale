"""Привязка Admin-runtime плагинов к ``app.state`` (F11, задача 12).

Родитель вызывает ``attach_admin_runtime(app, *, ports, service)`` из ``main.py``
(САМ; этот модуль ``main.py`` НЕ редактирует). Ключи выбраны ровно те, которых
ждёт ``api/v1/plugin_admin.py``:

* ``plugin_admin_ports``   — ``PluginAdminPorts`` (``_ports(request)``,
  ``isinstance``-проверка, иначе 503 ``PLUGIN_ADMIN_NOT_CONFIGURED``);
* ``plugin_admin_service`` — ``PluginAdminService`` (``_service(request)``,
  ``isinstance``-проверка, иначе 503 ``PLUGIN_ADMIN_NOT_CONFIGURED``).

Никаких дополнительных ключей и никаких «почти привязанных» состояний:
``attach_admin_runtime`` проверяет оба значения **до** записи в ``app.state``
и отказывает целиком (атомарность привязки). Это согласуется с
``wiring/state.py::STATE_KEYS`` (там уже лежит ``plugin_admin_service`` из
``attach_plugin_platform``) — мы не трогаем чужие ключи и не перетираем
``STATE_KEYS``, а лишь явно кладём требуемую пару для задачи 12.

Fail-closed
-----------
* ``attach_admin_runtime(app, ports=None|service=None)`` →
  ``PluginAdminRuntimeNotAttached``: выключенную/не собранную интеграцию НЕ
  привязываем «пустышкой», иначе роутер перестал бы отличать «подключено» от
  «не собрано»;
* ``require_admin_runtime(request)`` на app без обоих ключей → **503**
  ``PLUGIN_ADMIN_NOT_CONFIGURED`` (наследник ``AppError`` → problem+json через
  ``register_exception_handlers`` из ``main.py``);
* ключ есть, но значение не того типа / ``None`` → тоже 503, НЕ allow.
"""

from __future__ import annotations

from typing import Any, Final, Protocol, runtime_checkable

from prodavan.application.plugins.admin.ports import PluginAdminPorts
from prodavan.application.plugins.admin.service import PluginAdminService
from prodavan.domain.errors import AppError

#: Ключи, которые ждёт ``api/v1/plugin_admin.py``. Экспортируются для тестов
#: родительской интеграции: «почти привязанный» runtime = дыра.
STATE_KEYS: Final[tuple[str, ...]] = (
    "plugin_admin_ports",
    "plugin_admin_service",
)

ADMIN_RUNTIME_UNAVAILABLE_CODE: Final[str] = "PLUGIN_ADMIN_NOT_CONFIGURED"


class PluginAdminRuntimeNotAttached(RuntimeError):
    """Попытка привязать не собранный Admin-runtime (fail-closed)."""


@runtime_checkable
class AppStateLike(Protocol):
    """Структурный минимум ``FastAPI``/``Starlette`` приложения."""

    @property
    def state(self) -> Any: ...


@runtime_checkable
class RequestLike(Protocol):
    """Структурный минимум ``fastapi.Request`` для зависимости."""

    @property
    def app(self) -> AppStateLike: ...


def attach_admin_runtime(
    app: Any,
    *,
    ports: PluginAdminPorts | None,
    service: PluginAdminService | None,
) -> None:
    """Положить ``plugin_admin_ports`` и ``plugin_admin_service`` в ``app.state``.

    Проверки выполняются до любой записи: при отказе ``app.state`` остаётся
    прежним (не «один ключ привязан, второй нет»). Родитель должен просто НЕ
    звать функцию, если интеграция не собрана — тогда роутер отдаст 503.
    """

    if ports is None:
        raise PluginAdminRuntimeNotAttached(
            "PluginAdminPorts is None; do not attach a stub — routers must answer 503"
        )
    if not isinstance(ports, PluginAdminPorts):
        raise PluginAdminRuntimeNotAttached(
            f"expected PluginAdminPorts, got {type(ports).__name__!r}"
        )
    if service is None:
        raise PluginAdminRuntimeNotAttached(
            "PluginAdminService is None; do not attach a stub — routers must answer 503"
        )
    if not isinstance(service, PluginAdminService):
        raise PluginAdminRuntimeNotAttached(
            f"expected PluginAdminService, got {type(service).__name__!r}"
        )

    state = getattr(app, "state", None)
    if state is None:
        raise PluginAdminRuntimeNotAttached("app has no .state to attach admin runtime to")

    setattr(state, "plugin_admin_ports", ports)
    setattr(state, "plugin_admin_service", service)


def detach_admin_runtime(app: Any) -> None:
    """Снять оба ключа (для тестов/горячего выключения флага).

    После ``detach`` ``require_admin_runtime`` обязан давать 503. Не трогает
    чужие ключи ``state`` (в т.ч. ``STATE_KEYS`` из ``wiring/state.py``).
    """

    state = getattr(app, "state", None)
    if state is None:
        return
    for key in STATE_KEYS:
        if hasattr(state, key):
            delattr(state, key)


def _unavailable(detail: str) -> AppError:
    """503 ``AppError`` → problem+json через ``main.py`` handlers."""

    return AppError(
        code=ADMIN_RUNTIME_UNAVAILABLE_CODE,
        title="Plugin admin not configured",
        status=503,
        detail=detail,
    )


def require_admin_runtime(request: Any) -> tuple[PluginAdminPorts, PluginAdminService]:
    """FastAPI-зависимость: вернуть пару (ports, service) или 503 (fail-closed).

    Использование (делает родитель/задачи 12)::

        @router.get("/...")
        def handler(runtime=Depends(require_admin_runtime)):
            ports, service = runtime

    На app без ``plugin_admin_ports`` / ``plugin_admin_service`` → 503
    ``PLUGIN_ADMIN_NOT_CONFIGURED``. Никаких «создай сервис на ходу» и
    «верни None»: отсутствие интеграции = явный отказ, не молчаливый успех
    (повторяет контракт ``plugin_admin.py::_ports`` / ``::_service``).
    """

    app = getattr(request, "app", None)
    state = getattr(app, "state", None) if app is not None else None
    if state is None:
        raise _unavailable("app.state отсутствует: Admin API плагинов закрыто (503)")

    ports = getattr(state, "plugin_admin_ports", None)
    if not isinstance(ports, PluginAdminPorts):
        raise _unavailable(
            "PluginAdminPorts не подключён: родительская интеграция F11 не выполнена"
        )
    service = getattr(state, "plugin_admin_service", None)
    if not isinstance(service, PluginAdminService):
        raise _unavailable(
            "PluginAdminService не подключён: родительская интеграция F11 не выполнена"
        )
    return ports, service


__all__ = [
    "ADMIN_RUNTIME_UNAVAILABLE_CODE",
    "STATE_KEYS",
    "AppStateLike",
    "PluginAdminRuntimeNotAttached",
    "RequestLike",
    "attach_admin_runtime",
    "detach_admin_runtime",
    "require_admin_runtime",
]
