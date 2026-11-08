"""Привязка собранной платформы к ``app.state`` (F11).

Родитель вызывает ``attach_plugin_platform(app, platform)`` из ``main.py``
(САМ; этот модуль ``main.py`` НЕ редактирует). Ключи ``app.state`` согласованы
с тем, чего ждут задачи 04/11/12:

* ``plugin_platform`` — сам ``PluginPlatform``;
* ``plugin_capability_service`` — готовый ``PluginCapabilityService``
  (ждёт задача 04, ``transport.bridge.get_capability_service``);
* ``plugin_capability_service_factory`` — callable без аргументов,
  возвращающий тот же сервис (второй вариант привязки задачи 04; фабрика
  пригодится, если родитель решит создавать сервис на запрос);
* ``plugin_keyring`` — ``KeyRing`` (``transport.deps.resolve_identity_config``);
* ``plugin_revocation`` — ``DurableGrantRevocation`` (и ``check(claims)``, и
  ``is_active``/``is_revoked``; тот же контракт ждёт ``GrantRevocation``);
* ``plugin_identity`` — ``PluginIdentityConfig`` задачи 04 на случай, если
  роутер читает единый объект вместо двух отдельных ключей.

Дополнительно (не ломая чужих ключей) кладутся ``plugin_artifact_verifier``,
``plugin_artifact_registry``, ``plugin_adapter_registry``, ``plugin_tenant_db``,
``plugin_object_store``, ``plugin_egress``, ``plugin_activator``,
``plugin_gateway_auth``, ``plugin_gateway_proxy``, ``plugin_admin_service``,
``plugin_event_dispatcher``, ``plugin_jwks`` — чтобы роутеры задач 05..12
могли достать свои зависимости без повторной сборки.

Fail-closed
-----------
* ``attach_plugin_platform(app, None)`` -> ``PluginPlatformNotAttached``:
  выключенную платформу НЕ привязываем «пустышкой», иначе
  ``get_plugin_platform`` перестал бы отличать «включено» от «не собрано»;
* ``get_plugin_platform(request)`` на непривязанном app -> **503**
  ``PLUGIN_PLATFORM_UNAVAILABLE`` (``starlette.exceptions.HTTPException``,
  чтобы ``register_exception_handlers`` из ``main.py`` сформировал
  problem+json);
* платформа с ``enabled=False`` в ``app.state`` -> тоже 503;
* factory, вернувшая ``None``/бросившая исключение -> 503, НЕ allow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Protocol, runtime_checkable

from starlette.exceptions import HTTPException

from prodavan.application.plugins.transport.deps import PluginIdentityConfig
from prodavan.application.plugins.wiring.platform import PluginPlatform

# Все ключи, которые обязана выставить ``attach_plugin_platform``. Тест
# сверяет их наличие целиком: «почти привязанная» платформа = дыра.
STATE_KEYS: Final[tuple[str, ...]] = (
    "plugin_platform",
    "plugin_capability_service",
    "plugin_capability_service_factory",
    "plugin_keyring",
    "plugin_revocation",
    "plugin_identity",
    "plugin_artifact_verifier",
    "plugin_artifact_registry",
    "plugin_adapter_registry",
    "plugin_tenant_db",
    "plugin_object_store",
    "plugin_egress",
    "plugin_activator",
    "plugin_gateway_auth",
    "plugin_gateway_proxy",
    "plugin_admin_service",
    "plugin_event_dispatcher",
    "plugin_jwks",
)

# Ключи, прямо требуемые ТЗ («кладёт ... ждёт задача 04 ...»).
REQUIRED_STATE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "plugin_platform",
        "plugin_capability_service",
        "plugin_capability_service_factory",
        "plugin_keyring",
        "plugin_revocation",
    }
)

PLATFORM_UNAVAILABLE_CODE: Final[str] = "PLUGIN_PLATFORM_UNAVAILABLE"


class PluginPlatformNotAttached(RuntimeError):
    """Попытка привязать не собранную/выключенную платформу (fail-closed)."""


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


@dataclass(frozen=True, slots=True)
class _CapabilityServiceFactory:
    """Неизменяемая callable-фабрика, возвращающая готовый сервис.

    Задача 04 пробует ``factory()``, затем ``factory(request)``. Поддерживаем
    обе формы: лишний аргумент игнорируется, но ``None`` никогда не вернётся
    (сервис привязан на этапе сборки).
    """

    service: object

    def __call__(self, *_args: Any, **_kwargs: Any) -> object:
        return self.service


def attach_plugin_platform(app: Any, platform: PluginPlatform | None) -> PluginPlatform:
    """Положить все ключи платформы в ``app.state`` и вернуть ``platform``.

    ``platform is None`` (``enabled=False``) или ``platform.enabled is False``
    -> ``PluginPlatformNotAttached``: родитель должен просто НЕ звать эту
    функцию, а роутеры в отсутствие ключей отдадут 503 через
    ``get_plugin_platform`` / зависимости задач 04/11/12.

    Никаких чтений ``settings`` и dev-фолбэков внутри: всё уже собрано.
    """
    if platform is None:
        raise PluginPlatformNotAttached(
            "plugin platform is disabled (build_plugin_platform returned None); "
            "do not attach a stub — routers must answer 503"
        )
    if not isinstance(platform, PluginPlatform):
        raise PluginPlatformNotAttached(
            f"expected PluginPlatform, got {type(platform).__name__!r}"
        )
    if not platform.enabled:
        raise PluginPlatformNotAttached(
            "refusing to attach a platform with enabled=False; routers must answer 503"
        )

    state = getattr(app, "state", None)
    if state is None:
        raise PluginPlatformNotAttached("app has no .state to attach the plugin platform to")

    identity = PluginIdentityConfig(
        keyring=platform.keyring,
        revocation=platform.revocation,
    )
    values: dict[str, Any] = {
        "plugin_platform": platform,
        "plugin_capability_service": platform.capability_service,
        "plugin_capability_service_factory": _CapabilityServiceFactory(platform.capability_service),
        "plugin_keyring": platform.keyring,
        "plugin_revocation": platform.revocation,
        "plugin_identity": identity,
        "plugin_artifact_verifier": platform.artifact_verifier,
        "plugin_artifact_registry": platform.artifact_registry,
        "plugin_adapter_registry": platform.registry,
        "plugin_tenant_db": platform.tenant_db,
        "plugin_object_store": platform.object_store,
        "plugin_egress": platform.egress,
        "plugin_activator": platform.activator,
        "plugin_gateway_auth": platform.gateway_auth,
        "plugin_gateway_proxy": platform.gateway_proxy,
        "plugin_admin_service": platform.admin,
        "plugin_event_dispatcher": platform.event_dispatcher,
        "plugin_jwks": platform.jwks,
    }
    # Проверяем только обязательные по ТЗ ключи: второстепенные ключи
    # zone-сервисов не обязаны быть не-None (JWKS для HS256 — {"keys": []}).
    for key in REQUIRED_STATE_KEYS:
        if key not in values or values[key] is None:
            raise PluginPlatformNotAttached(
                f"required app.state key {key!r} is missing; refusing partial attach"
            )
    for key, value in values.items():
        setattr(state, key, value)
    return platform


def detach_plugin_platform(app: Any) -> None:
    """Снять все ключи платформы (для тестов/горячего выключения флага).

    После ``detach`` ``get_plugin_platform`` обязан давать 503 — используется
    в unit-тестах как «непривязанный app». Не трогает чужие ключи ``state``.
    """
    state = getattr(app, "state", None)
    if state is None:
        return
    for key in STATE_KEYS:
        if hasattr(state, key):
            delattr(state, key)


def _unavailable(detail: str) -> HTTPException:
    """503 в формате, который ``main.py`` превратит в problem+json."""
    return HTTPException(
        status_code=503,
        detail={
            "code": PLATFORM_UNAVAILABLE_CODE,
            "title": "Service Unavailable",
            "message": detail,
        },
    )


def get_plugin_platform(request: Any) -> PluginPlatform:
    """FastAPI-зависимость: вернуть платформу или 503 (fail-closed).

    Использование в роутерах (делает родитель/задачи 04..12)::

        @router.get("/...")
        def handler(platform: PluginPlatform = Depends(get_plugin_platform)):
            ...

    На непривязанном ``app`` (``enabled=False`` либо родитель ещё не вызвал
    ``attach_plugin_platform``) -> 503 ``PLUGIN_PLATFORM_UNAVAILABLE``.
    Никаких «создай платформу на ходу» и «верни None»: отсутствие интеграции
    = явный отказ, не молчаливый успех (§0.5 плана, §4.1 v4).
    """
    app = getattr(request, "app", None)
    state = getattr(app, "state", None) if app is not None else None
    platform = getattr(state, "plugin_platform", None) if state is not None else None
    if platform is None:
        raise _unavailable(
            "plugin platform is not attached to app.state; plugin surface is closed (503)"
        )
    if not isinstance(platform, PluginPlatform):
        raise _unavailable(
            "app.state.plugin_platform has an unexpected type; plugin surface is closed (503)"
        )
    if not platform.enabled:
        raise _unavailable("plugin platform is disabled by configuration (503)")
    return platform


def get_capability_service_factory(request: Any) -> Any:
    """Вернуть ``PluginCapabilityService`` через привязанную factory (или 503).

    Согласовано с ``transport.bridge.get_capability_service``: если готовый
    сервис по какой-то причине отсутствует, но factory есть — зовём её.
    """
    app = getattr(request, "app", None)
    state = getattr(app, "state", None) if app is not None else None
    if state is None:
        raise _unavailable("app.state is missing; plugin capability service is closed (503)")
    service = getattr(state, "plugin_capability_service", None)
    if service is not None:
        return service
    factory = getattr(state, "plugin_capability_service_factory", None)
    if not callable(factory):
        raise _unavailable("plugin capability service is not configured (503)")
    try:
        built = factory()
    except Exception as exc:  # noqa: BLE001 — fail-closed: любая ошибка = 503
        raise _unavailable(f"capability service factory failed: {type(exc).__name__}") from exc
    if built is None:
        raise _unavailable("capability service factory returned None (503)")
    return built


__all__ = [
    "PLATFORM_UNAVAILABLE_CODE",
    "REQUIRED_STATE_KEYS",
    "STATE_KEYS",
    "AppStateLike",
    "PluginPlatformNotAttached",
    "RequestLike",
    "attach_plugin_platform",
    "detach_plugin_platform",
    "get_capability_service_factory",
    "get_plugin_platform",
]
