"""Единая точка F11-композиции plugin-платформы: ОДИН вызов для ``main.py``.

ПРОБЛЕМА (аудит F11, блокеры №1 и №4)
--------------------------------------
``main.py`` монтирует plugin-роутеры, но НЕ вызывает ни ``build_plugin_platform``,
ни ``attach_plugin_platform``, ни ``install_plugin_gateway_runtime``, ни
``attach_admin_runtime``. Ключей ``app.state`` нет -> роутеры честно отдают 503.
Родителю нужен ОДИН вызов, который делает всё::

    from prodavan.application.plugins.wiring import install_plugin_platform

    install_plugin_platform(app, settings, session=session, redis=redis)

Этот модуль и есть тот вызов. Он НЕ редактирует ``main.py`` (граница родителя).
В прод-контуре in-memory подмена БД/Redis ЗАПРЕЩЕНА (A10).

Точный порядок (шаги a..i ТЗ)
-----------------------------
    a) build_plugin_repositories(session, redis)   -> SQL/Redis-репозитории A10
    b) build_runtime_ports(settings, ...)          -> cosign/k8s/state/proxy
    c) build_plugin_admin_ports(...)               -> PluginAdminPorts (все 12 полей)
    d) build_gateway_runtime(...)                 -> PluginGatewayRuntime
    e) build_plugin_platform(settings, grant_store=..., cosign=...,
                             admin_ports=..., <порты зон>)
    f) attach_plugin_platform(app, platform)       -> STATE_KEYS wiring (19)
    g) install_plugin_gateway_runtime(app, runtime)
    h) attach_admin_runtime(app, ports=..., service=...)
    i) register_plugin_surface_guard(app)         -> ровно один раз по app.state-флагу

Guard ДО attach (решение по ``transport/guard.py``)
---------------------------------------------------
``register_plugin_surface_guard`` делает ``app.add_middleware(...)``. Starlette
фиксирует стек middleware при первой сборке ``build_middleware_stack()``;
``add_middleware`` ПОСЛЕ этого уже не меняет стек. Поэтому guard регистрируется
**ДО** attach-фазы и ДО включения роутеров — так plugin-токен на ``/agent/*``
получает 403, а не 404 (критерий №17). Guard читает ``app.state.plugin_keyring``
/ ``plugin_revocation`` в момент ЗАПРОСА (``resolve_identity_config``), а не в
момент регистрации, поэтому порядок относительно записи ключей не критичен —
критичен порядок относительно фиксации middleware и роутинга. Флаг
``plugin_surface_guard_registered`` исключает двойную регистрацию.

Авто-композиция и честный список долгов
---------------------------------------
Родитель передаёт ТОЛЬКО ``session`` + ``redis``. Всё остальное этот модуль
строит из РЕАЛЬНЫХ фабрик worktree (SQL-репозитории, ``RuntimePorts``,
``BearerPlatformAdminAdapter``, MinIO/k8s/proxy-адаптеры, тонкие адаптеры зон).
Для портов, у которых в worktree ещё НЕТ прод-фабрики (задачи A10/14/15),
ставится ``_UnwiredPort`` — объект, удовлетворяющий structural Protocol, но
бросающий ЯВНЫЙ отказ при первом же вызове (тот же fail-closed паттерн, что
``wiring/platform.py::_MissingPort`` / ``UnavailableAdapter``). Список таких
портов публикуется в ``app.state.plugin_unwired_ports`` для родителя/Verify Dev:
это НЕ «тихий успех», а диагностируемый долг F11.

Fail-closed
-----------
* ``enabled=False`` -> ``None``, ``app.state`` НЕ тронут (роутеры отдают 503);
* ``enabled=True`` + ``session is None`` / ``redis is None`` ->
  ``PluginCompositionError`` (in-memory подмена источника истины запрещена);
* отсутствие любой build-функции при ``enabled=True`` -> ``PluginCompositionError``
  с именем шага, а НЕ тихий пропуск и НЕ ``None``;
* ЛЮБАЯ ошибка на любом шаге -> ``PluginCompositionError`` + **полный откат**
  уже записанных ``app.state``-ключей (см. ``_Composer``); «частично привязанной»
  платформы быть не должно.

Ленивые импорты
---------------
ВСЕ перечисленные build-функции импортируются ВНУТРИ функции (``_load``):
соседние агенты ещё дописывают файлы, и модуль обязан подниматься, даже если
какой-то из них временно отсутствует. На уровне модуля — только stdlib.

Тесты написаны, НО НЕ ЗАПУСКАЮТСЯ (verification freeze миграции).
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import os
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Final

logger = logging.getLogger(__name__)

#: Флаг в ``app.state``: guard уже зарегистрирован ровно один раз. Имя намеренно
#: не совпадает ни с одним из ``wiring.state.STATE_KEYS`` / admin / gateway.
PLUGIN_SURFACE_GUARD_STATE_KEY: Final[str] = "plugin_surface_guard_registered"

#: Диагностика: порты без прод-фабрики (задачи A10/14/15). НЕ секреты.
UNWIRED_PORTS_STATE_KEY: Final[str] = "plugin_unwired_ports"

#: Порты, которые ПРИВЯЗАНЫ, но работают in-memory (кластеру нужна БД/Redis,
#: A10). Это НЕ ``_UnwiredPort`` и НЕ долг «совсем не подключено», поэтому
#: они НЕ должны попадать в ``plugin_unwired_ports`` — иначе Verify Dev видит
#: ложный долг. Отдельный ключ для честной диагностики.
IN_MEMORY_PORTS_STATE_KEY: Final[str] = "plugin_in_memory_ports"

#: Префикс имени класса, означающего «порт работает в памяти процесса».
_IN_MEMORY_PORT_PREFIX: Final[str] = "InMemory"


def _in_memory_port_names(ports: dict[str, Any]) -> list[str]:
    """Вернуть имена портов, чей ПРИВЯЗАННЫЙ объект живёт в памяти процесса.

    Диагностика должна отражать фактическое состояние композиции, а не
    намерение вызывающего: список наполняется по настоящему классу объекта,
    поэтому подмена SQL/Redis-порта на in-memory заглушку видна сразу.
    """
    names: list[str] = []
    for name, value in ports.items():
        if value is not None and type(value).__name__.startswith(_IN_MEMORY_PORT_PREFIX):
            names.append(name)
    return names
RUNTIME_PORTS_STATE_KEY: Final[str] = "plugin_runtime_ports"
RUNTIME_SHUTDOWN_STATE_KEY: Final[str] = "plugin_runtime_shutdown_registered"
TENANT_DB_ADMIN_STATE_KEY: Final[str] = "plugin_tenant_db_admin_connection"
TENANT_DB_ADMIN_LIFECYCLE_STATE_KEY: Final[str] = "plugin_tenant_db_admin_lifecycle"
DIGEST_PAYLOAD_STORE_STATE_KEY: Final[str] = "plugin_digest_payload_store"
BODY_DIGEST_RESOLVER_FACTORY_STATE_KEY: Final[str] = "plugin_body_digest_resolver_factory"
DIGEST_PAYLOAD_SHUTDOWN_STATE_KEY: Final[str] = "plugin_digest_payload_shutdown_registered"
MCP_DISCOVERY_STATE_KEY: Final[str] = "plugin_mcp_discovery"
CAPABILITY_LIFECYCLE_STATE_KEY: Final[str] = "plugin_capability_lifecycle"
_TENANT_DB_ADMIN_LIFESPAN_NAME: Final[str] = "plugin-tenant-db-admin"
_MINIO_STS_LIFESPAN_NAME: Final[str] = "plugin-minio-sts-probe"
_RUNTIME_LIFESPAN_NAME: Final[str] = "plugin-runtime"
_PAYLOAD_LIFESPAN_NAME: Final[str] = "plugin-digest-payload-store"
_CAPABILITY_LIFESPAN_NAME: Final[str] = "plugin-capability-platform"
MINIO_STS_LIFECYCLE_STATE_KEY: Final[str] = "plugin_minio_sts_lifecycle"

# CI publication is an emergency-disabled trust contour of its own.  These
# variables intentionally do not fall back to Keycloak, employee auth, the
# plugin platform flag, or any agent/pod credential.
PLUGIN_CI_PUBLICATION_ENABLED_ENV: Final[str] = "PLUGIN_CI_PUBLICATION_ENABLED"
PLUGIN_CI_OIDC_AUDIENCE_ENV: Final[str] = "PLUGIN_CI_OIDC_AUDIENCE"
_PLUGIN_CI_LIFESPAN_NAME: Final[str] = "plugin-ci-publication-artifacts"


class LazyRedisManager:
    """Прозрачный прокси к ``RedisManager`` на время ``create_app()``.

    ``create_app()`` выполняется ДО ``lifespan.startup()``, поэтому
    ``get_redis_manager()`` в этот момент возвращает ``None``, а
    ``install_plugin_platform`` по A10 требует не-None зависимость (in-memory
    подмена источника истины запрещена). Прокси НЕ хранит состояние: каждый
    доступ к атрибуту резолвит реальный менеджер в момент ВЫЗОВА, уже после
    старта lifespan.

    Важно: это НЕ объект только с ``.client``. ``RedisRateLimitAdapter``
    обращается к ``.eval`` / ``.set`` / ``.enabled`` напрямую, а
    ``RedisPluginQuota`` — к ``.client``. Поэтому ``__getattr__`` делегирует
    ВСЁ, а ``client`` объявлен свойством (иначе ``hasattr(client, "client")``
    в quota-адаптере не сработал бы). До старта Redis любой вызов даёт
    явный ``RuntimeError`` -> fail-closed deny, а не «тихий allow».
    """

    __slots__ = ()

    def _manager(self) -> Any:
        from prodavan.core.infra.redis_manager import get_redis_manager

        manager = get_redis_manager()
        if manager is None:
            raise RuntimeError(
                "RedisManager is not started yet (lifespan startup pending)"
            )
        return manager

    @property
    def client(self) -> Any:
        return self._manager().client

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._manager(), "enabled", False))

    def __getattr__(self, name: str) -> Any:
        # __slots__ + property уже обработали client/enabled/_manager.
        return getattr(self._manager(), name)


_OWN_STATE_KEYS: Final[tuple[str, ...]] = (
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
    TENANT_DB_ADMIN_STATE_KEY,
    TENANT_DB_ADMIN_LIFECYCLE_STATE_KEY,
    "plugin_object_store",
    "plugin_egress",
    "plugin_activator",
    "plugin_gateway_auth",
    "plugin_gateway_proxy",
    "plugin_admin_service",
    "plugin_event_dispatcher",
    "plugin_jwks",
    "plugin_gateway_runtime",
    "plugin_admin_ports",
    PLUGIN_SURFACE_GUARD_STATE_KEY,
    UNWIRED_PORTS_STATE_KEY,
    IN_MEMORY_PORTS_STATE_KEY,
    RUNTIME_PORTS_STATE_KEY,
    RUNTIME_SHUTDOWN_STATE_KEY,
    DIGEST_PAYLOAD_STORE_STATE_KEY,
    BODY_DIGEST_RESOLVER_FACTORY_STATE_KEY,
    DIGEST_PAYLOAD_SHUTDOWN_STATE_KEY,
    MCP_DISCOVERY_STATE_KEY,
    CAPABILITY_LIFECYCLE_STATE_KEY,
)

__all__ = [
    "PLUGIN_SURFACE_GUARD_STATE_KEY",
    "UNWIRED_PORTS_STATE_KEY",
    "IN_MEMORY_PORTS_STATE_KEY",
    "RUNTIME_PORTS_STATE_KEY",
    "RUNTIME_SHUTDOWN_STATE_KEY",
    "TENANT_DB_ADMIN_STATE_KEY",
    "TENANT_DB_ADMIN_LIFECYCLE_STATE_KEY",
    "DIGEST_PAYLOAD_STORE_STATE_KEY",
    "BODY_DIGEST_RESOLVER_FACTORY_STATE_KEY",
    "DIGEST_PAYLOAD_SHUTDOWN_STATE_KEY",
    "MCP_DISCOVERY_STATE_KEY",
    "CAPABILITY_LIFECYCLE_STATE_KEY",
    "LazyRedisManager",
    "PLUGIN_CI_OIDC_AUDIENCE_ENV",
    "PLUGIN_CI_PUBLICATION_ENABLED_ENV",
    "PluginCompositionError",
    "install_plugin_ci_publication",
    "install_plugin_platform",
    "install_plugin_platform_for_tests",
    "plugin_ci_publication_enabled",
]


class PluginCompositionError(RuntimeError):
    """Ошибка ОДНОГО шага F11-композиции (fail-closed: startup обязан упасть).

    ``step`` — имя шага (``repositories``, ``runtime_ports``, ``admin_ports``,
    ``gateway_runtime``, ``platform``, ``attach_platform``, ``gateway_install``,
    ``admin_attach``, ``guard``, ``verify``), чтобы родитель сразу видел
    неподключённый стык. В ``detail`` — только тип исключения и безопасный текст:
    секреты/DSN/токены/kubeconfig/stdout cosign ЗАПРЕЩЕНЫ (§4.5, критерий №11).
    """

    def __init__(self, step: str, detail: str) -> None:
        self.step = step
        self.detail = detail
        super().__init__(f"plugin composition failed at step {step!r}: {detail}")


# ---------------------------------------------------------------------------
# Fail-closed заглушка для портов БЕЗ прод-фабрики (НЕ «тихий успех»).
# ---------------------------------------------------------------------------



class _UnwiredPort:
    """Structural-совместимый порт, который ЯВНО отказывает при любом вызове.

    Удовлетворяет ``@runtime_checkable`` Protocol через ``__getattr__`` (все
    методы «есть»), но каждый вызов бросает ``RuntimeError`` с именем порта.
    Это тот же контракт, что ``wiring/platform.py::_MissingPort`` /
    ``UnavailableAdapter``: платформа привязывается, а неподключённый вызов
    даёт system deny (503), а не «200 с None». Имя порта попадает в
    ``app.state.plugin_unwired_ports`` — родитель видит долг F11.
    """

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, method: str) -> Any:
        def refuse(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError(
                f"plugin port {self._name!r}.{method}() is not wired "
                "(F11: connect a real adapter via install_plugin_platform overrides)"
            )

        refuse.__name__ = method
        return refuse


class _StaticOriginNotConfigured:
    """Fail-closed static-origin port: НЕ подставлять plugin backend под статику.

    Отдельный static-origin (§5.2/§13.3) — always-running, БЕЗ cookie и БЕЗ
    capability-токена. Молчаливый fallback ``static_http = runtime.backend_http``
    нарушал эту изоляцию: статика уходила на plugin backend. Здесь — тот же
    контракт, что ``wiring/platform.py`` (``GatewayProxy(static_http=None)``):
    любой запрос статики получает ``GatewayError(STATIC_ORIGIN_NOT_CONFIGURED)``
    (503), а не проксирование на backend и не «молчаливый успех». Порт
    опционален на конструкторе, поэтому в ``plugin_unwired_ports`` НЕ
    добавляется (это не долг обязательной привязки, а штатный fail-closed).
    """

    __slots__ = ()

    async def send(self, _request: Any) -> Any:
        from prodavan.application.plugins.gateway.errors import (
            GatewayError,
            GatewayErrorCode,
        )

        raise GatewayError(
            GatewayErrorCode.STATIC_ORIGIN_NOT_CONFIGURED,
            detail="plugin static origin is not configured (fail-closed)",
        )


class _TenantDbAdminLifecycle:
    """Shared startup, shutdown and readiness for one tenant admin adapter."""

    def __init__(self, admin: Any | None) -> None:
        self.admin = admin

    @property
    def name(self) -> str:
        return _TENANT_DB_ADMIN_LIFESPAN_NAME

    async def startup(self) -> None:
        open_pool = getattr(self.admin, "open", None)
        if callable(open_pool):
            await open_pool()

    async def shutdown(self) -> None:
        close_pool = getattr(self.admin, "close", None)
        if callable(close_pool):
            await close_pool()

    async def health(self) -> bool:
        if self.admin is None or isinstance(self.admin, _UnwiredPort):
            return False
        probe = getattr(self.admin, "health", None)
        if callable(probe):
            return bool(await probe())
        return True


class _MinioStsProbeLifecycle:
    """Async startup/revalidation hook for the one shared MinioSts adapter."""

    def __init__(self, sts: Any) -> None:
        self.sts = sts
        self._revalidate_task: asyncio.Task[Any] | None = None

    @property
    def name(self) -> str:
        return _MINIO_STS_LIFESPAN_NAME

    async def startup(self) -> None:
        # Registration happens during create_app; only this async initialization
        # hook may perform IO. Failure deliberately leaves the operation unwired.
        await self.sts.probe(force=True)
        config = getattr(self.sts, "config", None)
        if (
            self._revalidate_task is None
            and bool(getattr(config, "sts_enabled", False))
            and bool(getattr(config, "sts_probe_enabled", False))
        ):
            self._revalidate_task = asyncio.create_task(self._revalidate())

    async def _revalidate(self) -> None:
        # Refresh before expiry; failed probes use bounded retry backoff. This
        # task is owned by lifespan/worker initialization, never by module import.
        while True:
            config = self.sts.config
            if self.sts.scoped_credentials_wired:
                delay = max(1, int(config.sts_probe_revalidate_seconds) - 1)
            else:
                delay = int(config.sts_probe_retry_seconds)
            await asyncio.sleep(delay)
            try:
                await self.sts.probe(force=True)
            except Exception:
                # A driver failure must not leave permanent stale availability or
                # terminate future revalidation. Probe itself never logs credentials.
                self.sts.invalidate()

    async def shutdown(self) -> None:
        task = self._revalidate_task
        self._revalidate_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        # The platform-owned adapter closes the client; this resource only drops
        # availability so a stale success cannot survive shutdown/error.
        self.sts.invalidate()

    async def health(self) -> bool | None:
        config = getattr(self.sts, "config", None)
        if not bool(getattr(config, "sts_enabled", False)) or not bool(
            getattr(config, "sts_probe_enabled", False)
        ):
            return None
        return bool(self.sts.scoped_credentials_wired)


class _AcloseLifecycle:
    """Shutdown-only LifespanResource для владельца с идемпотентным aclose().

    Startup намеренно пустой: композиция уже собрала владельца (без IO при
    регистрации/старте). Закрытие идёт ТОЛЬКО через LifespanManager — в
    обратном порядке регистрации (shutdown_all), как и остальные ресурсы.
    Никаких Starlette on_startup/on_shutdown: на кастомном
    FastAPI(lifespan=...) они не вызываются, а на pinned Starlette (>=1.6)
    add_event_handler/lifespan_context удалены совсем.
    """

    __slots__ = ("_name", "owner")

    def __init__(self, name: str, owner: Any) -> None:
        self._name = name
        self.owner = owner

    @property
    def name(self) -> str:
        return self._name

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        await self.owner.aclose()

    async def health(self) -> bool | None:
        # Shutdown-only ресурс: отдельной readiness-проверки нет.
        return None


def _lifespan_manager(
    app: Any,
    overrides: Mapping[str, Any] | None,
    step: str,
) -> Any:
    """Единый shared LifespanManager — единственный lifecycle-шов (fail-closed).

    Источник: app.state.lifespan_manager (main) либо явная передача
    overrides['lifespan_manager'] (plugin gateway, который по контракту
    не публикует менеджер в app.state). Старых fallback'ов на
    app.add_event_handler/app.router.lifespan_context НЕТ: первый не
    работает на кастомном lifespan и удалён в Starlette 1.6+, второй удалён
    вместе с ним. Отсутствие менеджера = ошибка композиции, не тихий пропуск.
    """

    state = getattr(app, "state", None)
    manager = getattr(state, "lifespan_manager", None)
    if manager is None and overrides is not None:
        manager = overrides.get("lifespan_manager")
    if manager is None or not callable(getattr(manager, "register", None)):
        raise PluginCompositionError(
            step,
            "shared application LifespanManager is unavailable; plugin lifecycle "
            "must be registered through LifespanManager.register (Starlette "
            "on_startup/on_shutdown is unsupported on the pinned framework)",
        )
    return manager


def _register_shutdown_resource(
    app: Any,
    owner: Any,
    composer: _Composer,
    *,
    step: str,
    resource_name: str,
    overrides: Mapping[str, Any] | None = None,
) -> _AcloseLifecycle:
    """Зарегистрировать aclose()-ресурс один раз; владелец остаётся singleton."""

    manager = _lifespan_manager(app, overrides, step)
    get_resource = getattr(manager, "get", None)
    existing = get_resource(resource_name) if callable(get_resource) else None
    if existing is not None:
        if getattr(existing, "owner", None) is not owner:
            raise PluginCompositionError(
                step, f"a different {resource_name} lifecycle is already registered"
            )
        return existing
    resource = _wrap(step, _AcloseLifecycle, resource_name, owner)
    _wrap(step, manager.register, resource)
    composer.mark_lifecycle(manager, resource_name)
    return resource


# ---------------------------------------------------------------------------
# Ленивые импорты: модуль не падает, если соседний файл ещё дописывается.
# ---------------------------------------------------------------------------


def _load(step: str, dotted: str, name: str) -> Any:
    """ЛЕНИВО импортировать ``name`` из ``dotted``.

    При ``enabled=True`` отсутствие функции = ``PluginCompositionError(step)``
    (НЕ ``None``, НЕ тихий пропуск).
    """

    try:
        module = importlib.import_module(dotted)
    except (ImportError, ModuleNotFoundError, SyntaxError, AttributeError) as exc:
        raise PluginCompositionError(
            step,
            f"{dotted}.{name} недоступен ({type(exc).__name__}); fail-closed: "
            "сборка включённой платформы без этого шага запрещена",
        ) from exc
    except Exception as exc:  # noqa: BLE001 — ошибка инициализации = ошибка композиции
        raise PluginCompositionError(
            step, f"импорт {dotted} упал: {type(exc).__name__}"
        ) from exc
    try:
        obj = getattr(module, name)
    except AttributeError as exc:
        raise PluginCompositionError(
            step, f"в {dotted} нет атрибута {name!r} (без тихого пропуска)"
        ) from exc
    if not callable(obj):
        raise PluginCompositionError(step, f"{dotted}.{name} не является callable")
    return obj


def _cfg(settings: Any) -> Any:
    """Спроецировать ``Settings`` -> ``PluginPlatformConfig`` (лениво).

    Дубль guard'ов ``Settings`` живёт в ``PluginPlatformConfig.from_settings``:
    платформа обязана отказаться собираться даже с кастомным объектом настроек.
    ``enabled`` читается ДО любых build-шагов.
    """

    from prodavan.application.plugins.wiring.config import PluginPlatformConfig

    return PluginPlatformConfig.from_settings(settings)


def _wrap(step: str, fn: Any, *args: Any, **kwargs: Any) -> Any:
    """ЛЮБАЯ ошибка build-функции -> ``PluginCompositionError(step)``."""

    try:
        return fn(*args, **kwargs)
    except PluginCompositionError:
        raise
    except Exception as exc:  # noqa: BLE001 — fail-closed: любая ошибка = отказ целиком
        # Driver/config exceptions may contain DSNs, secret refs, credentials or
        # response payloads. Only the exception type crosses the composition log.
        raise PluginCompositionError(
            step, f"{type(exc).__name__}: operation failed"
        ) from None


# ---------------------------------------------------------------------------
# Откат app.state: «частично привязанной» платформы быть не должно.
# ---------------------------------------------------------------------------


class _Composer:
    """Запомнить записанные ключи ``app.state`` и откатить при ошибке."""

    def __init__(self, app: Any) -> None:
        self._app = app
        self._state = getattr(app, "state", None)
        if self._state is None:
            raise PluginCompositionError("app", "app has no .state to attach plugin platform to")
        self._before: dict[str, tuple[bool, Any]] = {}
        self._lifecycle: list[tuple[Any, str]] = []
        for key in _OWN_STATE_KEYS:
            try:
                self._before[key] = (True, getattr(self._state, key))
            except AttributeError:
                self._before[key] = (False, None)
        self._touched: list[str] = []

    def mark(self, *keys: str) -> None:
        for key in keys:
            if key not in self._touched:
                self._touched.append(key)

    def mark_lifecycle(self, manager: Any, name: str) -> None:
        """Запомнить одну регистрацию LifespanManager для отката."""

        self._lifecycle.append((manager, name))

    def rollback(self) -> None:
        for manager, name in reversed(self._lifecycle):
            try:
                unregister = getattr(manager, "unregister", None)
                if callable(unregister):
                    unregister(name)
            except Exception:  # noqa: BLE001 — откат не маскирует исходную ошибку
                logger.warning(
                    "plugin composition rollback failed to unregister lifespan %s", name
                )
        self._lifecycle.clear()
        for key in reversed(self._touched):
            existed, old = self._before.get(key, (False, None))
            try:
                if existed:
                    setattr(self._state, key, old)
                else:
                    try:
                        delattr(self._state, key)
                    except AttributeError:
                        pass
            except Exception:  # noqa: BLE001 — откат не маскирует исходную ошибку
                logger.warning("plugin composition rollback failed for app.state.%s", key)
        self._touched.clear()


# ---------------------------------------------------------------------------
# Шаг i: guard ровно один раз (ДО attach).
# ---------------------------------------------------------------------------


def _register_guard_once(app: Any, composer: _Composer) -> None:
    if bool(getattr(composer._state, PLUGIN_SURFACE_GUARD_STATE_KEY, False)):
        logger.debug("plugin surface guard already registered; skipping duplicate")
        return
    register = _load(
        "guard",
        "prodavan.application.plugins.transport.guard",
        "register_plugin_surface_guard",
    )
    _wrap("guard", register, app)
    # Флаг ТОЛЬКО после успеха: при падении guard повторный вызов пробует снова.
    setattr(composer._state, PLUGIN_SURFACE_GUARD_STATE_KEY, True)
    composer.mark(PLUGIN_SURFACE_GUARD_STATE_KEY)


def _register_runtime_shutdown(
    app: Any,
    runtime: Any,
    composer: _Composer,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> None:
    """Attach one idempotent runtime shutdown LifespanResource and rollback state."""
    state = composer._state
    if bool(getattr(state, RUNTIME_SHUTDOWN_STATE_KEY, False)):
        if getattr(state, RUNTIME_PORTS_STATE_KEY, None) is not runtime:
            raise PluginCompositionError(
                "runtime_lifecycle", "a different plugin runtime is already registered"
            )
        return
    if not callable(getattr(runtime, "aclose", None)):
        raise PluginCompositionError(
            "runtime_lifecycle",
            "RuntimePorts must support idempotent async shutdown",
        )
    _register_shutdown_resource(
        app,
        runtime,
        composer,
        step="runtime_lifecycle",
        resource_name=_RUNTIME_LIFESPAN_NAME,
        overrides=overrides,
    )
    setattr(state, RUNTIME_PORTS_STATE_KEY, runtime)
    composer.mark(RUNTIME_PORTS_STATE_KEY)
    setattr(state, RUNTIME_SHUTDOWN_STATE_KEY, True)
    composer.mark(RUNTIME_SHUTDOWN_STATE_KEY)


def _publish_digest_payload_store(
    redis: Any,
    overrides: Mapping[str, Any],
    composer: _Composer,
) -> Any:
    """Publish one content-addressed store and its network resolver factory."""
    from prodavan.application.plugins.capabilities.payloads import (
        DigestPayloadResolverFactory,
        DigestPayloadStore,
    )

    state = composer._state
    existing = getattr(state, DIGEST_PAYLOAD_STORE_STATE_KEY, None)
    supplied = overrides.get("digest_payload_store")
    if existing is not None and supplied is not None and existing is not supplied:
        raise PluginCompositionError(
            "payload_store", "a different digest payload store is already installed"
        )
    store = supplied or existing
    if store is None:
        client = overrides.get("redis_client")
        if client is None:
            from prodavan.infrastructure.plugins.capabilities.redis import ManagerRedisClient

            client = ManagerRedisClient(redis)
        store = DigestPayloadStore(
            client=client,
            config=overrides.get("digest_payload_config"),
        )
    if not isinstance(store, DigestPayloadStore):
        raise PluginCompositionError(
            "payload_store", "digest_payload_store must be a DigestPayloadStore"
        )
    if existing is not None and store is not existing:
        raise PluginCompositionError(
            "payload_store", "digest payload store singleton identity changed"
        )

    factory = getattr(state, BODY_DIGEST_RESOLVER_FACTORY_STATE_KEY, None)
    if factory is None:
        factory = DigestPayloadResolverFactory(store=store)
    elif not callable(factory) or getattr(factory, "store", None) is not store:
        raise PluginCompositionError(
            "payload_store", "body digest resolver factory is bound to another store"
        )
    setattr(state, DIGEST_PAYLOAD_STORE_STATE_KEY, store)
    setattr(state, BODY_DIGEST_RESOLVER_FACTORY_STATE_KEY, factory)
    composer.mark(DIGEST_PAYLOAD_STORE_STATE_KEY, BODY_DIGEST_RESOLVER_FACTORY_STATE_KEY)
    return store


def _register_digest_payload_shutdown(
    app: Any,
    store: Any,
    composer: _Composer,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> None:
    """Register one idempotent close without closing the shared Redis manager."""
    state = composer._state
    if getattr(state, DIGEST_PAYLOAD_STORE_STATE_KEY, None) is not store:
        raise PluginCompositionError(
            "payload_lifecycle", "digest payload store is not published"
        )
    if bool(getattr(state, DIGEST_PAYLOAD_SHUTDOWN_STATE_KEY, False)):
        return
    if not callable(getattr(store, "aclose", None)):
        raise PluginCompositionError(
            "payload_lifecycle",
            "DigestPayloadStore must support idempotent async shutdown",
        )
    _register_shutdown_resource(
        app,
        store,
        composer,
        step="payload_lifecycle",
        resource_name=_PAYLOAD_LIFESPAN_NAME,
        overrides=overrides,
    )
    setattr(state, DIGEST_PAYLOAD_SHUTDOWN_STATE_KEY, True)
    composer.mark(DIGEST_PAYLOAD_SHUTDOWN_STATE_KEY)


def _register_tenant_db_admin_lifecycle(
    app: Any,
    resource: _TenantDbAdminLifecycle,
    composer: _Composer,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> None:
    """Register the adapter once with shared lifespan and publish diagnostics.

    Только LifespanManager.register: никаких Starlette startup/shutdown событий.
    """
    manager = _lifespan_manager(app, overrides, "tenant_db_admin_lifecycle")
    get_resource = getattr(manager, "get", None)
    existing = get_resource(resource.name) if callable(get_resource) else None
    if existing is not None:
        if existing is not resource:
            raise PluginCompositionError(
                "tenant_db_admin_lifecycle",
                "a different tenant admin lifecycle is already registered",
            )
        return
    _wrap("tenant_db_admin_lifecycle", manager.register, resource)
    composer.mark_lifecycle(manager, resource.name)
    setattr(composer._state, TENANT_DB_ADMIN_STATE_KEY, resource.admin)
    setattr(composer._state, TENANT_DB_ADMIN_LIFECYCLE_STATE_KEY, resource)
    composer.mark(TENANT_DB_ADMIN_STATE_KEY, TENANT_DB_ADMIN_LIFECYCLE_STATE_KEY)


def _register_minio_sts_lifecycle(
    app: Any,
    sts: Any,
    composer: _Composer,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> None:
    """Register probe startup without constructor/import-time IO or a second STS.

    Только LifespanManager.register: никаких Starlette startup/shutdown событий.
    """
    if sts is None:
        return
    resource = _MinioStsProbeLifecycle(sts)
    manager = _lifespan_manager(app, overrides, "minio_sts_probe")
    get_resource = getattr(manager, "get", None)
    existing = get_resource(resource.name) if callable(get_resource) else None
    if existing is not None:
        if existing is not resource and getattr(existing, "sts", None) is not sts:
            raise PluginCompositionError(
                "minio_sts_probe", "a different MinioSts lifecycle is already registered",
            )
        return
    _wrap("minio_sts_probe", manager.register, resource)
    composer.mark_lifecycle(manager, resource.name)
    setattr(composer._state, MINIO_STS_LIFECYCLE_STATE_KEY, resource)
    composer.mark(MINIO_STS_LIFECYCLE_STATE_KEY)


def _register_capability_shutdown(
    app: Any,
    platform: Any,
    composer: _Composer,
    *,
    overrides: Mapping[str, Any] | None = None,
) -> None:
    """Register one idempotent capability platform close via the shared lifespan."""
    if not callable(getattr(platform, "aclose", None)):
        raise PluginCompositionError(
            "capability_lifecycle",
            "platform must support idempotent async capability shutdown",
        )
    _register_shutdown_resource(
        app,
        platform,
        composer,
        step="capability_lifecycle",
        resource_name=_CAPABILITY_LIFESPAN_NAME,
        overrides=overrides,
    )
    setattr(composer._state, CAPABILITY_LIFECYCLE_STATE_KEY, True)
    composer.mark(CAPABILITY_LIFECYCLE_STATE_KEY)


def _close_runtime_after_failed_build(runtime: Any) -> None:
    """Best-effort async cleanup without masking the composition exception."""
    if runtime is None:
        return
    close = getattr(runtime, "aclose", None)
    if not callable(close):
        return
    try:
        result = close()
        if not inspect.isawaitable(result):
            return

        async def wait_for_close() -> None:
            await result

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(wait_for_close())
        else:
            loop.create_task(wait_for_close())
    except Exception:  # noqa: BLE001 — rollback preserves the startup failure
        logger.warning("plugin runtime rollback close failed")


# ---------------------------------------------------------------------------
# Авто-композиция: реальные сервисы зон + честные заглушки без фабрики.
# ---------------------------------------------------------------------------


def _shared_secret_store(overrides: Mapping[str, Any]) -> Any:
    """Resolve one control-plane RoutingSecretStore, never a runtime-local backend."""
    store = overrides.get("secret_store")
    if store is None:
        from prodavan.infrastructure.secrets.store import get_secret_store

        store = get_secret_store()
    if store is None:
        from prodavan.application.plugins.capabilities.adapters._shared import (
            CapabilityConfigurationError,
        )

        raise CapabilityConfigurationError("secret_store_unwired")
    if not all(
        callable(getattr(store, method, None)) for method in ("get", "put", "delete")
    ):
        from prodavan.application.plugins.capabilities.adapters._shared import (
            CapabilityConfigurationError,
        )

        raise CapabilityConfigurationError("secret_store_contract_invalid")
    return store


def _minio_bucket_admin(secret_store: Any = None, settings: Any = None) -> Any | None:
    """Собрать реальный ``BucketAdminPort`` из env плагинового контура.

    ``MinioBucketAdmin`` уже реализует весь контракт (bucket-per-tenant,
    merge политик, квоты, fail-closed). Конфигурация читается ТОЛЬКО из
    ``PLUGIN_MINIO_*`` — фолбэка на root/S3_* нет (ADR-008, §5.4).

    Если env не заданы (unit-тесты, частичный dev-контур), НЕ роняем
    композицию: логируем и возвращаем ``None`` — вызывающий оставит честную
    fail-closed заглушку ``_UnwiredPort``. Запрос на bucket даст system deny,
    а не «200 с None». Это не «тихая деградация»: имя порта попадает в
    ``app.state.plugin_unwired_ports``.
    """
    try:
        from prodavan.infrastructure.plugins.minio.adapters import MinioBucketAdmin
        from prodavan.infrastructure.plugins.minio.config import MinioPluginConfig

        if secret_store is None:
            raise RuntimeError("SecretStore is required for PLUGIN_MINIO_*_REF")
        return MinioBucketAdmin(
            config=MinioPluginConfig.from_secret_refs(
                secret_store=secret_store,
                settings=settings,
            ),
            owns_bucket=True,
        )
    except Exception as exc:  # noqa: BLE001 — конфигурация/импорт ≠ падение startup
        logger.warning(
            "plugin MinIO bucket admin is not configured (%s); "
            "object capability stays fail-closed",
            type(exc).__name__,
        )
        return None


def _activation_mint(repos: Any, config: Any) -> Any | None:
    """``activation.MintPort``: перевыпуск короткого JWT при каждом wake.

    ``PluginActivator`` знает только ``plugin_instance_id``; полный grant
    берётся из SQL-репозитория A10 (``get_by_instance_id``), затем
    ``IdentityMintAdapter`` выпускает токен и ``MintedClaims`` собирается из
    ПРОВЕРЕННОГО payload. Источник истины один — БД грантов, секрет явный
    из ``config.claims_secret`` (без dev-фолбэков, §4.2/§13).

    Коллизия протоколов: ``admin.MintPort.mint(request=...)`` и
    ``activation.MintPort.mint(plugin_instance_id=..., reason=...)`` — РАЗНЫЕ
    контракты. Здесь реализуется именно activation-вариант.
    """
    try:
        from datetime import UTC, datetime

        from prodavan.application.plugins.activation.ports import MintedClaims
        from prodavan.application.plugins.adapters.identity_adapter import (
            IdentityMintAdapter,
        )
        from prodavan.application.plugins.admin.contracts import MintRequest
        from prodavan.application.plugins.identity.mint import peek_plugin_token

        store = getattr(repos, "grant_store", None)
        if store is None or not callable(getattr(store, "get_by_instance_id", None)):
            return None
        adapter = IdentityMintAdapter(secret=config.claims_secret)

        class _ActivationMint:
            async def mint(self, *, plugin_instance_id: str, reason: str) -> Any:
                record = await store.get_by_instance_id(
                    plugin_instance_id=plugin_instance_id
                )
                if record is None or not getattr(record, "is_active", True):
                    raise RuntimeError(
                        "no active CapabilityGrant for instance; refusing wake"
                    )
                issued = await adapter.mint(
                    request=MintRequest(
                        company_id=record.company_id,
                        plugin_id=record.plugin_id,
                        plugin_instance_id=record.plugin_instance_id,
                        manifest_version=record.manifest_version,
                        artifact_digest=record.artifact_digest,
                        grant_id=record.grant_id,
                        scopes=tuple(record.capabilities),
                        quota_ref=record.grant_id,
                    )
                )
                payload = peek_plugin_token(issued.token) or {}
                return MintedClaims(
                    token=issued.token,
                    jti=issued.jti,
                    issued_at=datetime.fromtimestamp(int(payload.get("iat", 0)), tz=UTC),
                    expires_at=issued.expires_at,
                    claims=dict(payload),
                )

        return _ActivationMint()
    except Exception as exc:  # noqa: BLE001 — конфигурация/импорт ≠ падение startup
        logger.warning(
            "plugin activation mint port is not configured (%s: %s); "
            "wake-up stays fail-closed",
            type(exc).__name__,
            str(exc)[:120],
        )
        return None


def _tenant_db_admin(settings: Any) -> Any | None:
    """Build asyncpg admin or leave a typed-unwired port without a fallback."""
    from prodavan.infrastructure.plugins.tenantdb import (
        AsyncpgAdminConnection,
        TenantAdminConfigError,
    )

    try:
        return AsyncpgAdminConnection.from_settings(settings)
    except TenantAdminConfigError as exc:
        # reason is a fixed safe code, never the URL or driver message.
        logger.warning(
            "plugin tenant-db admin port is not configured (%s); grant stays fail-closed",
            exc.reason,
        )
        return None


def _tenant_db_secrets(secret_store: Any = None) -> Any | None:
    """``tenantdb.SecretPort`` поверх существующего ``RoutingSecretStore``.

    Порт задачи 05 — ASYNC (``put``/``rotate``/``delete``), а
    ``RoutingSecretStore`` — синхронный. Тонкий мост НЕ хранит DSN в памяти:
    каждый вызов уходит в проверенный store, наружу возвращается только
    ``secret_ref`` (§5.5, критерий №11). ``rotate`` = delete старого ref +
    put нового значения, как требует контракт.

    Если store недоступен, возвращаем ``None`` -> честный ``_UnwiredPort``.
    """
    try:
        from prodavan.infrastructure.secrets.store import get_secret_store

        store = secret_store if secret_store is not None else get_secret_store()

        def _ref_for_key(key: str) -> str:
            from prodavan.config.settings import settings

            if settings.vault_addr:
                return f"vault://ai_keys/{key}"
            return f"file://ai_keys/{key}.secret"

        class _AsyncSecretStore:
            async def get(self, *, key: str) -> Any | None:
                from prodavan.application.plugins.tenantdb.service import StoredSecret

                secret_ref = _ref_for_key(key)
                try:
                    value = store.get(secret_ref)
                except Exception as exc:
                    if getattr(exc, "code", "") == "SECRET_NOT_FOUND":
                        return None
                    raise
                return StoredSecret(secret_ref=secret_ref, value=value)

            async def put(self, *, key: str, value: str) -> str:
                return store.put(key, value)

            async def rotate(self, *, key: str, value: str) -> str:
                # Сервис задачи 05 вызывает rotate(value="") при revoke, чтобы
                # инвалидировать DSN. FileSecretStore.put пустоту ОТВЕРГАЕТ
                # (422 "secret required"), поэтому для пустого значения
                # удаляем ref напрямую: старый DSN больше недоступен.
                if not value or not value.strip():
                    from prodavan.infrastructure.secrets.file_store import REF_PREFIX

                    store.delete(f"{REF_PREFIX}{key}.secret")
                    return ""
                return store.put(key, value)

            async def delete(self, *, secret_ref: str) -> None:
                store.delete(secret_ref)

        return _AsyncSecretStore()
    except Exception as exc:  # noqa: BLE001 — конфигурация/импорт ≠ падение startup
        logger.warning(
            "plugin tenant-db secret port is not configured (%s); "
            "DSN storage stays fail-closed",
            type(exc).__name__,
        )
        return None


def _zone_services(
    runtime: Any,
    repos: Any,
    config: Any,
    ov: dict[str, Any],
    unwired: list[str],
    *,
    settings: Any = None,
) -> dict[str, Any]:
    """Собрать РЕАЛЬНЫЕ сервисы зон для тонких admin-адаптеров.

    Один ``runtime`` (k8s/proxy/cosign) и один набор ``repos`` (SQL/Redis) на
    все зоны: адаптеры и платформа смотрят в ОДНО состояние (критерии №12/№15/№22).
    """

    def need(name: str) -> Any:
        value = ov.get(name)
        if value is None:
            unwired.append(name)
            value = _UnwiredPort(name)
        return value

    from prodavan.application.plugins.activation.activator import PluginActivator, SystemClock
    from prodavan.application.plugins.activation.state import IdlePolicy
    from prodavan.application.plugins.artifacts.registry import PluginArtifactRegistry
    from prodavan.application.plugins.artifacts.verification import (
        PLATFORM_VERSION,
        ArtifactVerifier,
        CompatibilityGate,
    )
    from prodavan.application.plugins.objects.service import PluginObjectStore
    from prodavan.application.plugins.tenantdb.service import TenantDatabaseService
    from prodavan.infrastructure.plugins.minio.adapters import MinioAuditAdapter

    verifier = ArtifactVerifier(
        runtime.cosign,
        validator=ov.get("manifest_validator"),
        identity=config.cosign_identity,
        issuer=config.cosign_issuer,
        compatibility=CompatibilityGate(platform_version=PLATFORM_VERSION),
    )
    artifact_registry = PluginArtifactRegistry(
        store=ov.get("artifact_store") or repos.artifact_store, verifier=verifier
    )
    object_buckets = ov.get("object_buckets")
    if object_buckets is None:
        minio_kwargs = {"settings": settings} if settings is not None else {}
        object_buckets = _minio_bucket_admin(ov.get("secret_store"), **minio_kwargs)
    minio_config = ov.get("minio_config") or getattr(object_buckets, "config", None)
    if minio_config is not None:
        # Reuse the exact resolved config in the typed objects adapter: refs are
        # resolved once and admin/STS cannot observe different credential versions.
        ov["minio_config"] = minio_config
    legacy = {"minio_sts_available", "io_test_sts_available"} & set(ov)
    if legacy:
        raise PluginCompositionError(
            "minio_sts_probe",
            f"legacy STS availability override is forbidden: {sorted(legacy)[0]}",
        )
    object_sts = ov.get("object_sts")
    if object_sts is None and minio_config is not None:
        from prodavan.infrastructure.plugins.minio.adapters import MinioSts

        # No constructor boolean is target evidence. The shared adapter starts
        # unwired and is probed only by the async lifespan resource below.
        object_sts = MinioSts(
            config=minio_config,
            sts_client=ov.get("minio_sts_client"),
        )
        ov["object_sts"] = object_sts
    object_store = PluginObjectStore(
        buckets=object_buckets,
        sts=object_sts,
        presigner=ov.get("object_presigner"),
        audit=MinioAuditAdapter(sink=repos.audit),
    )
    tenant_db_secrets = ov.get("tenant_db_secrets")
    if tenant_db_secrets is None:
        tenant_db_secrets = _tenant_db_secrets(ov.get("secret_store"))
    if tenant_db_secrets is None:
        tenant_db_secrets = need("tenant_db_secrets")
    tenant_db_admin = ov.get("tenant_db_admin")
    if tenant_db_admin is None:
        # The service converts None to typed ADMIN_UNAVAILABLE on every use.
        unwired.append("tenant_db_admin")
    tenant_db = TenantDatabaseService(
        admin=tenant_db_admin,
        secrets=tenant_db_secrets,
        audit=repos.audit,
        dsn_host=config.tenant_db_host,
        dsn_port=config.tenant_db_port,
        dsn_sslmode="require",
    )
    activator = PluginActivator(
        scale=runtime.scale,
        readiness=runtime.readiness,
        clock=ov.get("clock") or SystemClock(),
        mint=_activation_mint(repos, config) or need("activation_mint"),
        audit=repos.audit,
        policy=IdlePolicy(
            idle_ttl_seconds=float(config.idle_ttl_seconds),
            ready_timeout_seconds=float(config.ready_timeout_seconds),
            max_concurrent_wakes=int(config.max_concurrent_wakes),
        ),
        state_store=runtime.state_store,
        proxy=runtime.proxy,
    )
    return {
        "artifact_verifier": verifier,
        "artifact_registry": artifact_registry,
        "object_store": object_store,
        "object_buckets": object_buckets,
        "object_sts": object_sts,
        "tenant_db": tenant_db,
        "tenant_db_secrets": tenant_db_secrets,
        "activator": activator,
    }


def _webhook_secret_resolver(repos: Any, secrets: Any) -> Any:
    """Resolve ACTIVE grants, plus the strictly fenced first-grant projection."""
    from datetime import UTC, datetime

    from prodavan.application.plugins.admin.contracts import (
        CapabilityGrantRecord,
        PluginInstanceRecord,
        PluginInstanceStatus,
        PluginPublicationApproval,
    )
    from prodavan.application.plugins.admin.ports import webhook_secret_key
    from prodavan.application.plugins.tenantdb.service import StoredSecret

    grants = getattr(repos, "grant_store", None)
    instances = getattr(repos, "instance_store", None)
    approvals = getattr(repos, "publication_approvals", None)

    class _Resolver:
        async def _active_grant_allowed(
            self,
            record: CapabilityGrantRecord,
            *,
            company_id: str,
            plugin_id: str,
            plugin_instance_id: str,
            secret_ref: str,
        ) -> bool:
            if (
                not isinstance(record, CapabilityGrantRecord)
                or not record.is_active
                or record.revoked_at is not None
                or record.company_id != company_id
                or record.plugin_id != plugin_id
                or record.plugin_instance_id != plugin_instance_id
                or record.webhook_secret_ref != secret_ref
            ):
                return False
            expires = getattr(record, "expires_at", None)
            if expires is not None and (
                not isinstance(expires, datetime)
                or expires.tzinfo is None
                or expires.utcoffset() is None
                or expires <= datetime.now(UTC)
            ):
                return False
            # SQL implementations additionally verify the physical expires_at row.
            activity = getattr(grants, "is_active_async", None)
            if callable(activity) and not await activity(grant_id=record.grant_id):
                return False
            return True

        async def _first_grant_allowed(
            self,
            *,
            company_id: str,
            plugin_id: str,
            plugin_instance_id: str,
        ) -> bool:
            instance_getter = getattr(instances, "get", None)
            approval_getter = getattr(approvals, "get", None)
            if not callable(instance_getter) or not callable(approval_getter):
                return False
            instance = await instance_getter(company_id=company_id, plugin_id=plugin_id)
            if (
                not isinstance(instance, PluginInstanceRecord)
                or instance.status is not PluginInstanceStatus.INSTALLED
                or instance.company_id != company_id
                or instance.plugin_id != plugin_id
                or instance.plugin_instance_id != plugin_instance_id
            ):
                return False
            approval = await approval_getter(
                company_id=company_id,
                plugin_id=plugin_id,
                manifest_version=instance.manifest_version,
                artifact_digest=instance.artifact_digest,
            )
            return (
                isinstance(approval, PluginPublicationApproval)
                and approval.consumed_at is None
                and approval.company_id == company_id
                and approval.plugin_id == plugin_id
                and approval.manifest_version == instance.manifest_version
                and approval.artifact_digest == instance.artifact_digest
            )

        async def _regrant_allowed(
            self,
            record: CapabilityGrantRecord,
            *,
            company_id: str,
            plugin_id: str,
            plugin_instance_id: str,
            secret_ref: str,
        ) -> bool:
            # Re-grant rotates the ref before the replacement ACTIVE grant exists.
            # The revoked record's old ref/digest/expiry must not fence that rotation.
            del secret_ref
            if (
                not isinstance(record, CapabilityGrantRecord)
                or record.is_active
                or record.revoked_at is None
                or record.company_id != company_id
                or record.plugin_id != plugin_id
                or record.plugin_instance_id != plugin_instance_id
            ):
                return False
            instance_getter = getattr(instances, "get", None)
            approval_getter = getattr(approvals, "get", None)
            if not callable(instance_getter) or not callable(approval_getter):
                return False
            instance = await instance_getter(company_id=company_id, plugin_id=plugin_id)
            if (
                not isinstance(instance, PluginInstanceRecord)
                or instance.status
                not in {
                    PluginInstanceStatus.REVOKED,
                    # Schema rotation has already completed, but the replacement
                    # grant is not ACTIVE yet; consumed approval is still required.
                    PluginInstanceStatus.INSTALLED,
                }
                or instance.company_id != company_id
                or instance.plugin_id != plugin_id
                or instance.plugin_instance_id != plugin_instance_id
            ):
                return False
            approval = await approval_getter(
                company_id=company_id,
                plugin_id=plugin_id,
                manifest_version=instance.manifest_version,
                artifact_digest=instance.artifact_digest,
            )
            return (
                isinstance(approval, PluginPublicationApproval)
                and approval.consumed_at is not None
                and approval.company_id == company_id
                and approval.plugin_id == plugin_id
                and approval.manifest_version == instance.manifest_version
                and approval.artifact_digest == instance.artifact_digest
            )

        async def resolve(
            self,
            *,
            company_id: str,
            plugin_id: str,
            plugin_instance_id: str,
            secret_ref: str,
        ) -> str:
            getter = getattr(grants, "get", None)
            if not callable(getter) or not isinstance(secret_ref, str) or not secret_ref.strip():
                raise LookupError("webhook grant resolver is unavailable")
            record = await getter(company_id=company_id, plugin_id=plugin_id)
            if record is not None:
                allowed = await self._active_grant_allowed(
                    record,
                    company_id=company_id,
                    plugin_id=plugin_id,
                    plugin_instance_id=plugin_instance_id,
                    secret_ref=secret_ref,
                )
                if not allowed:
                    allowed = await self._regrant_allowed(
                        record,
                        company_id=company_id,
                        plugin_id=plugin_id,
                        plugin_instance_id=plugin_instance_id,
                        secret_ref=secret_ref,
                    )
            else:
                allowed = await self._first_grant_allowed(
                    company_id=company_id,
                    plugin_id=plugin_id,
                    plugin_instance_id=plugin_instance_id,
                )
            if not allowed:
                raise LookupError("webhook grant or artifact binding is unavailable")
            key = webhook_secret_key(
                company_id=company_id,
                plugin_id=plugin_id,
                plugin_instance_id=plugin_instance_id,
            )
            stored = await secrets.get(key=key)
            if (
                not isinstance(stored, StoredSecret)
                or stored.secret_ref != secret_ref
                or not isinstance(stored.value, str)
                or not stored.value.strip()
            ):
                raise LookupError("webhook secret is unavailable")
            return stored.value

    return _Resolver()


def _secret_projection(runtime: Any, tenant_secrets: Any, webhook_resolver: Any = None) -> Any:
    """Build projection only with a complete trusted lifecycle configuration."""
    from prodavan.infrastructure.plugins.runtime.config import RuntimeConfig
    from prodavan.infrastructure.plugins.runtime.errors import RuntimeConfigError
    from prodavan.infrastructure.plugins.runtime.secret_projection import (
        K8sSecretProjection,
    )

    try:
        config = getattr(runtime, "config", None)
        api = getattr(runtime, "lifecycle_api", None)
        if not isinstance(config, RuntimeConfig) or not callable(
            getattr(api, "request", None)
        ):
            raise RuntimeConfigError("shared lifecycle K8s client is not configured")
        if isinstance(tenant_secrets, _UnwiredPort):
            raise RuntimeConfigError("tenant SecretPort is not configured")
        gateway_url = config.internal_gateway_url()
        if gateway_url is None:
            raise RuntimeConfigError("trusted internal gateway_url is not configured")
        return K8sSecretProjection(
            config=config,
            tenant_secrets=tenant_secrets,
            webhook_resolver=webhook_resolver,
            gateway_url=gateway_url,
            api=api,
        )
    except RuntimeConfigError as exc:
        logger.warning(
            "plugin SecretProjection is not configured (%s); lifecycle secret "
            "projection stays unwired",
            type(exc).__name__,
        )
        return _UnwiredPort("secret_projection")


def _secret_delivery_control(
    *,
    store: Any,
    projection: Any,
    artifact_registry: Any,
    max_secret_bytes: int = 65536,
) -> Any | None:
    """Build token-free pre-delivery only with the full approved artifact registry."""
    if isinstance(projection, _UnwiredPort) or not all(
        callable(getattr(projection, method, None))
        for method in ("reconcile_secret_values", "revoke_secret")
    ):
        return None
    if store is None or not callable(getattr(store, "get", None)):
        return None
    if not callable(getattr(artifact_registry, "get_async", None)):
        return None
    from prodavan.application.plugins.capabilities.adapters.secrets import (
        ControlPlaneSecretDelivery,
    )

    return ControlPlaneSecretDelivery(
        store=store,
        projection=projection,
        artifact_registry=artifact_registry,
        max_secret_bytes=max_secret_bytes,
    )


def _deployment_admin(
    runtime: Any,
    repos: Any,
    instance_store: Any,
    artifact_store: Any | None = None,
    secret_projection: Any | None = None,
) -> Any:
    """Build the real admin lifecycle on the shared runtime K8s client."""
    from prodavan.infrastructure.plugins.runtime.deployment_admin import (
        K8sDeploymentAdmin,
    )
    from prodavan.infrastructure.plugins.runtime.errors import RuntimeConfigError

    lifecycle_api = getattr(runtime, "lifecycle_api", None)
    if not callable(getattr(lifecycle_api, "request", None)):
        raise RuntimeConfigError(
            "DeploymentAdmin requires the shared short-lived lifecycle K8s client"
        )
    return K8sDeploymentAdmin(
        config=getattr(runtime, "config", None),
        artifact_store=artifact_store or repos.artifact_store,
        instance_store=instance_store,
        secret_projection=secret_projection,
        api=lifecycle_api,
    )


def _admin_ports(
    runtime: Any, repos: Any, config: Any, ov: dict[str, Any], unwired: list[str],
    services_out: dict[str, Any], in_memory: list[str], *, settings: Any = None,
) -> Any:
    """Шаг (c): ``PluginAdminPorts`` через ``build_plugin_admin_ports``.

    Все 12 полей обязательны. preferably SQL-репозитории, а не in-memory:
    ``grant_store``/``audit`` из ``PluginRepositories``, ``artifact_store`` — SQL,
    ``revocation_source`` — ``DbGrantRevocation``, ``mint_secret`` из проверенного
    ``PluginPlatformConfig`` (без dev-фолбэков), ``platform`` — настоящая
    PlatformAdmin-auth (``BearerPlatformAdminAdapter``).
    """

    build = _load(
        "admin_ports",
        "prodavan.application.plugins.adapters.build",
        "build_plugin_admin_ports",
    )
    zone_kwargs = {"settings": settings} if settings is not None else {}
    services = _wrap(
        "admin_ports", _zone_services, runtime, repos, config, ov, unwired,
        **zone_kwargs,
    )
    # ОДНИ и те же реальные сервисы зон использует и platform (шаг e), и gateway
    # (шаг d): иначе admin и capabilities смотрели бы в разные объекты зон.
    services_out.update(services)

    def need(name: str) -> Any:
        value = ov.get(name)
        if value is None:
            unwired.append(name)
            value = _UnwiredPort(name)
        return value

    platform_admin = ov.get("platform_admin")
    if platform_admin is None:
        from prodavan.application.plugins.platform_admin.adapter import (
            BearerPlatformAdminAdapter,
        )

        platform_admin = _wrap("admin_ports", BearerPlatformAdminAdapter)
    routes = ov.get("routes")
    if routes is None:
        from prodavan.infrastructure.persistence.repositories.plugins.routes import (
            SqlRouteRegistry,
        )

        # The route registry must use the exact durable SQL unit-of-work owned
        # by the repository bundle. Never substitute process-local route state.
        grant_repository = repos.grant_store
        session_factory = getattr(grant_repository, "_session_factory", None)
        bound_session = getattr(grant_repository, "_session", None)
        if session_factory is None and bound_session is None:
            raise PluginCompositionError(
                "admin_ports",
                "SqlRouteRegistry requires the shared repository session factory",
            )
        routes = _wrap(
            "admin_ports",
            SqlRouteRegistry,
            session_factory=session_factory,
            session=bound_session,
        )
    instance_store = ov.get("instance_store") or getattr(repos, "instance_store", None)
    if instance_store is None:
        instance_store = need("instance_store")
    instance_resolver = ov.get("instance_resolver") or instance_store
    webhook_secrets = ov.get("webhook_secrets")
    if webhook_secrets is None:
        webhook_secrets = _tenant_db_secrets(ov.get("secret_store"))
    if webhook_secrets is None:
        webhook_secrets = need("webhook_secrets")
    webhook_resolver = ov.get("webhook_secret_resolver")
    if webhook_resolver is None:
        webhook_resolver = _webhook_secret_resolver(repos, webhook_secrets)
    secret_projection = ov.get("secret_projection")
    if secret_projection is None:
        secret_projection = _wrap(
            "admin_ports",
            _secret_projection,
            runtime,
            services["tenant_db_secrets"],
            webhook_resolver,
        )
        if isinstance(secret_projection, _UnwiredPort):
            unwired.append("secret_projection")
    # Reuse this exact projection for optional typed capability delivery too.
    # It remains owned by the runtime lifecycle, not by the secrets adapter.
    services_out["secret_projection"] = secret_projection
    secret_delivery_control = ov.get("secret_delivery_control")
    if secret_delivery_control is None:
        secret_delivery_control = _secret_delivery_control(
            store=ov.get("secret_store"),
            projection=secret_projection,
            artifact_registry=services["artifact_registry"],
            max_secret_bytes=ov.get("secret_max_bytes", 65536),
        )
    services_out["secret_delivery_control"] = secret_delivery_control
    deployment_admin = ov.get("deployment_admin")
    if deployment_admin is None:
        # Real create/patch/status/scale/delete lifecycle; missing RuntimeConfig
        # or durable stores is a startup error, never a silent successful grant.
        deployment_admin = _wrap(
            "admin_ports",
            _deployment_admin,
            runtime,
            repos,
            instance_store,
            ov.get("artifact_store") or repos.artifact_store,
            secret_projection,
        )
    update_lock = ov.get("update_lock")
    if update_lock is None:
        from prodavan.infrastructure.persistence.repositories.plugins.update_lock import (
            SqlPluginUpdateLock,
        )

        session_factory = getattr(repos.grant_store, "_session_factory", None)
        if session_factory is None:
            raise PluginCompositionError(
                "admin_ports",
                "SqlPluginUpdateLock requires the shared process session factory",
            )
        update_lock = _wrap(
            "admin_ports", SqlPluginUpdateLock, session_factory=session_factory
        )
    installation_scopes = ov.get("installation_scopes")
    if installation_scopes is None:
        from prodavan.application.plugins.installation_scope.repository import (
            SqlInstallationScopeRepository,
        )

        session_factory = getattr(repos.grant_store, "_session_factory", None)
        if session_factory is None:
            raise PluginCompositionError(
                "admin_ports",
                "SqlInstallationScopeRepository requires the shared session factory",
            )
        installation_scopes = SqlInstallationScopeRepository(
            session_factory=session_factory
        )
    kwargs: dict[str, Any] = {
        "platform": platform_admin,
        "tenant_db_service": services["tenant_db"],
        "object_store_service": services["object_store"],
        "bucket_admin": services["object_buckets"],
        "artifact_registry": services["artifact_registry"],
        "artifact_store": ov.get("artifact_store") or repos.artifact_store,
        "revocation_source": ov.get("revocation_source") or repos.grant_revocation,
        "mint_secret": config.claims_secret,
        "activator": services["activator"],
        "instance_resolver": instance_resolver,
        "instance_store": instance_store,
        "deployment_admin": deployment_admin,
        # Required constructor kwarg of the current build_plugin_admin_ports
        # signature (adapters/build.py); replace() below keeps the same value.
        "webhook_secrets": webhook_secrets,
        "update_lock": update_lock,
        "installation_scopes": installation_scopes,
        "grant_store": ov.get("admin_grant_store") or repos.grant_store,
        "publication_approvals": (
            ov.get("publication_approvals")
            or getattr(repos, "publication_approvals", None)
        ),
        "routes": routes,
        "audit": ov.get("admin_audit") or repos.audit,
    }
    ports = _wrap("admin_ports", build, **kwargs)
    return replace(
        ports,
        webhook_secrets=webhook_secrets,
        secret_delivery=secret_delivery_control,
        update_lock=update_lock,
        installation_scopes=installation_scopes,
    )


def _gateway_sessions() -> Any | None:
    """Реальный ``SessionPort``: JWKS-валидатор + SQL-каталог сотрудников.

    ``SessionAdapter`` и ``SqlEmployeeDirectory`` уже реализованы в
    ``infrastructure/plugins/adapters/gateway_adapters.py``: Bearer/JWTF
    проверяется существующим ``get_jwt_validator()``, а ``employee ∈ company``
    читается из ``EmployeeRow``/``MembershipRow`` через process session
    factory. НЕ in-memory: источник истины — та же БД, что у остального API.

    Если фабрика сессий недоступна (частичный контур/тесты), возвращаем
    ``None`` -> вызывающий оставит fail-closed ``_UnwiredPort`` с именем в
    ``plugin_unwired_ports``. Не «тихий allow».
    """
    try:
        from prodavan.infrastructure.persistence.database import get_session_factory
        from prodavan.infrastructure.plugins.adapters.gateway_adapters import (
            SessionAdapter,
            SqlEmployeeDirectory,
        )

        return SessionAdapter(directory=SqlEmployeeDirectory(get_session_factory()))
    except Exception as exc:  # noqa: BLE001 — конфигурация/импорт ≠ падение startup
        logger.warning(
            "plugin gateway session port is not configured (%s: %s); "
            "session auth stays fail-closed",
            type(exc).__name__,
            str(exc)[:120],
        )
        return None


def _gateway_grants(repos: Any, services: Mapping[str, Any] | None = None) -> Any | None:
    """Реальный ``GrantPort`` поверх SQL-репозитория ``CapabilityGrant``.

    ``GrantAdapter`` (infrastructure/plugins/adapters/gateway_adapters.py)
    ждёт ``GrantStorePort.active_grant(plugin_id, company_id) -> GrantRecord``,
    а SQL-репозиторий A10 даёт ``get(company_id, plugin_id) ->
    CapabilityGrantRecord``. Тонкий мост маппит поля, включая egress-политику
    ИЗ БД (критерий №22: не из краткоживущего JWT). Источник истины один —
    ``repos.grant_store``; НЕ in-memory дубль.
    """
    try:
        from prodavan.infrastructure.plugins.adapters.gateway_adapters import (
            GrantAdapter,
            GrantRecord,
        )

        store = getattr(repos, "grant_store", None)
        if store is None or not callable(getattr(store, "get", None)):
            return None
        registry = (services or {}).get("artifact_registry")

        class _SqlGrantStore:
            def __init__(self, sql_store: Any) -> None:
                self._store = sql_store

            async def active_grant(
                self, *, plugin_id: str, company_id: str
            ) -> Any | None:
                record = await self._store.get(
                    company_id=company_id, plugin_id=plugin_id
                )
                if record is None:
                    return None
                policy = record.egress_policy
                if registry is None:
                    raise RuntimeError("signed artifact registry is required for webhook routes")
                get_async = getattr(registry, "get_async", None)
                if not callable(get_async):
                    raise RuntimeError("signed artifact registry async getter is required")
                artifact = await get_async(
                    plugin_id=record.plugin_id, version=record.manifest_version
                )
                if artifact is None or artifact.digest != record.artifact_digest:
                    raise RuntimeError("signed manifest does not match grant artifact digest")
                manifest = getattr(artifact, "manifest", None)
                raw_webhooks = manifest.get("webhooks") if isinstance(manifest, Mapping) else None
                if not isinstance(raw_webhooks, (list, tuple)) or any(
                    not isinstance(path, str) or not path.startswith("/webhook/")
                    for path in raw_webhooks
                ):
                    raise RuntimeError("signed manifest webhooks must be absolute /webhook/ paths")
                webhooks = tuple(dict.fromkeys(raw_webhooks))
                raw_public_pages = (
                    manifest.get("public_pages", [])
                    if isinstance(manifest, Mapping)
                    else None
                )
                if not isinstance(raw_public_pages, (list, tuple)) or any(
                    not isinstance(path, str) or not path.startswith("/")
                    for path in raw_public_pages
                ):
                    raise RuntimeError("signed manifest public_pages must be absolute paths")
                public_pages = frozenset(raw_public_pages)
                return GrantRecord(
                    grant_id=record.grant_id,
                    plugin_id=record.plugin_id,
                    company_id=record.company_id,
                    plugin_instance_id=record.plugin_instance_id,
                    active=bool(getattr(record, "is_active", True)),
                    operations=frozenset(record.capabilities),
                    public_pages=public_pages,
                    egress_mode=str(getattr(policy, "mode", "none")),
                    egress_allowed_hosts=tuple(getattr(policy, "allowed_hosts", ()) or ()),
                    egress_allowed_paths=tuple(getattr(policy, "allowed_paths", ()) or ()),
                    egress_approved_for_full=bool(
                        getattr(policy, "approved_for_full", False)
                    ),
                    artifact_digest=record.artifact_digest,
                    manifest_version=record.manifest_version,
                    webhooks=webhooks,
                )

        return GrantAdapter(_SqlGrantStore(store), artifact_registry=registry)
    except Exception as exc:  # noqa: BLE001 — конфигурация/импорт ≠ падение startup
        logger.warning(
            "plugin gateway grant port is not configured (%s: %s); "
            "grant lookups stay fail-closed",
            type(exc).__name__,
            str(exc)[:120],
        )
        return None


def _gateway_secrets(repos: Any) -> Any | None:
    """``SecretPort`` через ``SecretAdapter`` + резолвер refs из SQL-grant'а.

    ``SecretAdapter`` сам ходит в существующий ``RoutingSecretStore`` и
    поддерживает ротацию пары ключей (§7.5). Резолвер здесь НЕ выдумывает
    secret_ref: он читает его из того же SQL-репозитория ``CapabilityGrant``,
    что и ``gateway_grants``. Ref выдаётся при grant и принимается только для
    ACTIVE записи с совпадающим tenant. Legacy grant без ref возвращает ``None``;
    ``SecretAdapter`` даёт типизированный ``503 SECRET_UNAVAILABLE``, а не
    ``_UnwiredPort``/500. Fail-closed: вебхук без подписи НЕ принимается.
    """
    try:
        from prodavan.infrastructure.plugins.adapters.gateway_adapters import (
            SecretAdapter,
            SecretKeyRing,
        )

        store = getattr(repos, "grant_store", None)
        if store is None:
            return None

        async def _refs(plugin_id: str, company_id: str) -> Any | None:
            getter = getattr(store, "get", None)
            record = (
                await getter(company_id=company_id, plugin_id=plugin_id)
                if callable(getter)
                else None
            )
            if (
                record is None
                or not record.is_active
                or record.revoked_at is not None
                or record.company_id != company_id
                or record.plugin_id != plugin_id
            ):
                return None
            ref = getattr(record, "webhook_secret_ref", None)
            if not isinstance(ref, str) or not ref.strip():
                return None
            return SecretKeyRing(active_ref=ref)

        return SecretAdapter(refs=_refs)
    except Exception as exc:  # noqa: BLE001 — конфигурация/импорт ≠ падение startup
        logger.warning(
            "plugin gateway secret port is not configured (%s: %s); "
            "webhook HMAC stays fail-closed",
            type(exc).__name__,
            str(exc)[:120],
        )
        return None


def _gateway_runtime(
    runtime: Any, repos: Any, redis: Any, config: Any,
    ov: dict[str, Any], unwired: list[str],
    services: Mapping[str, Any] | None = None,
) -> Any:
    """Шаг (d): ``PluginGatewayRuntime`` через ``build_gateway_runtime``.

    Автоматически (реализации ЕСТЬ): ``hmac_verifier`` (``Sha256HmacAdapter``),
    ``rate_limit`` (``RedisRateLimitAdapter(redis)``), ``audit`` (``AuditAdapter``
    поверх append-only SQL-sink через тонкий ``write``-адаптер), ``activation``
    (``ActivationAdapter`` -> ``PluginActivator``), ``backend_http``
    из ``RuntimePorts``. ``static_http`` НЕ берётся из ``RuntimePorts``: без
    явного override ``gateway_static_http`` ставится fail-closed
    ``_StaticOriginNotConfigured`` (STATIC_ORIGIN_NOT_CONFIGURED при запросе,
    как platform path), а НЕ молчаливое проксирование статики на plugin
    backend. У ``SessionPort``/``GrantPort``/``SecretPort`` нет
    прод-фабрики (A10/родитель) -> ``_UnwiredPort`` с явным отказом при вызове.
    """

    build = _load(
        "gateway_runtime",
        "prodavan.infrastructure.plugins.adapters.build",
        "build_gateway_runtime",
    )
    from prodavan.application.plugins.gateway.auth import DEFAULT_LIMITS
    from prodavan.infrastructure.plugins.adapters.gateway_adapters import (
        ActivationAdapter,
        AuditAdapter,
        RedisRateLimitAdapter,
        Sha256HmacAdapter,
    )

    def need(name: str) -> Any:
        value = ov.get(name)
        if value is None:
            unwired.append(name)
            value = _UnwiredPort(name)
        return value

    class _SinkWriter:
        """``AuditAdapter`` ждёт ``async write(record)``; SQL-sink даёт append_event."""

        def __init__(self, sink: Any) -> None:
            self._sink = sink

        async def write(self, record: Mapping[str, Any]) -> None:
            await self._sink.append_event(
                str(record.get("event_type") or "plugin_gateway"), details=dict(record)
            )

    audit = ov.get("gateway_audit_port")
    if audit is None:
        audit = _wrap("gateway_runtime", AuditAdapter, _SinkWriter(repos.audit))
    activation = ov.get("gateway_activation_port")
    if activation is None:
        # Тот же PluginActivator, что в admin/platform: один state_store/CAS
        # на все контуры (критерий №13/№14, «wake до доставки»).
        activator = (services or {}).get("activator") or _gateway_activator(
            runtime, ov, need
        )
        activation = _wrap(
            "gateway_runtime",
            ActivationAdapter,
            activator=activator,
            instance_resolver=ov.get("gateway_instance_resolver")
            or getattr(repos, "instance_store", None)
            or need("gateway_instance_resolver"),
            readiness=runtime.readiness,
        )
    sessions = ov.get("gateway_sessions")
    if sessions is None:
        sessions = _gateway_sessions()
    if sessions is None:
        sessions = need("gateway_sessions")
    grants = ov.get("gateway_grants")
    if grants is None:
        grants = _gateway_grants(repos, services)
    if grants is None:
        grants = need("gateway_grants")
    secrets = ov.get("gateway_secrets")
    if secrets is None:
        secrets = _gateway_secrets(repos)
    if secrets is None:
        secrets = need("gateway_secrets")
    rate_limit = ov.get("gateway_rate_limit_port") or RedisRateLimitAdapter(redis)
    required_rate_limit_methods = (
        "allow",
        "claim_delivery_lease",
        "commit_delivery",
        "release_delivery",
    )
    missing_rate_limit_methods = tuple(
        name for name in required_rate_limit_methods
        if not callable(getattr(rate_limit, name, None))
    )
    if missing_rate_limit_methods:
        raise PluginCompositionError(
            "gateway_runtime",
            "RateLimitPort не реализует delivery lease contract: "
            + ", ".join(missing_rate_limit_methods),
        )
    kwargs: dict[str, Any] = {
        "sessions": sessions,
        "grants": grants,
        "secrets": secrets,
        "rate_limit": rate_limit,
        "audit": audit,
        "hmac_verifier": ov.get("gateway_hmac_port") or Sha256HmacAdapter(),
        "activation": activation,
        "backend_http": ov.get("gateway_backend_http") or runtime.backend_http,
        # НЕ fallback на runtime.backend_http: отдельный static-origin (§5.2/
        # §13.3) — always-running, БЕЗ cookie/capability-токена. Без явного
        # override — fail-closed STATIC_ORIGIN_NOT_CONFIGURED при запросе
        # статики, как в wiring/platform.py (GatewayProxy(static_http=None)).
        "static_http": ov.get("gateway_static_http") or _StaticOriginNotConfigured(),
        "limits": ov.get("gateway_limits") or DEFAULT_LIMITS,
        "shell_origin": config.shell_origin,
    }
    return _wrap("gateway_runtime", build, **kwargs)


def _gateway_activator(runtime: Any, ov: dict[str, Any], need: Any) -> Any:
    from prodavan.application.plugins.activation.activator import PluginActivator, SystemClock
    from prodavan.application.plugins.activation.state import IdlePolicy

    # Редкий fallback: в штатном пути _gateway_runtime берёт УЖЕ собранный
    # activator из services (с реальным _activation_mint). Здесь config/repos
    # недоступны, поэтому оставляем честную fail-closed заглушку, а не
    # дублируем сборку mint с неполным контекстом.
    return PluginActivator(
        scale=runtime.scale,
        readiness=runtime.readiness,
        clock=ov.get("clock") or SystemClock(),
        mint=need("activation_mint"),
        audit=ov.get("activation_audit") or _UnwiredPort("activation_audit"),
        policy=IdlePolicy.defaults(),
        state_store=runtime.state_store,
        proxy=runtime.proxy,
    )



def _capability_runtime_bindings(
    runtime: Any,
    repos: Any,
    ov: Mapping[str, Any],
    services: Mapping[str, Any] | None = None,
) -> tuple[Any, Any, Any]:
    """Build canonical backend executors; no static Service or mounted SA fallback."""
    from prodavan.infrastructure.plugins.runtime.egress_transport import (
        DurableGrantEgressPolicy,
        K8sEgressTransport,
    )
    from prodavan.infrastructure.plugins.runtime.pod_executor import K8sPodExecutor

    instance_store = ov.get("instance_store") or getattr(repos, "instance_store", None)
    artifact_store = ov.get("artifact_store") or getattr(repos, "artifact_store", None)
    grant_store = ov.get("grant_store") or getattr(repos, "grant_store", None)
    # The zone-composed activator is authoritative for every capability runtime.
    # Overrides are considered only when that shared service is genuinely absent.
    activator = (services or {}).get("activator")
    if activator is None:
        activator = ov.get("activator") or getattr(runtime, "activator", None)
    common = {
        "config": getattr(runtime, "config", None),
        "instance_store": instance_store,
        "artifact_store": artifact_store,
        "api": getattr(runtime, "lifecycle_api", None),
        "token_provider": getattr(runtime, "token_provider", None),
        "activator": activator,
    }
    pod_executor = ov.get("pod_executor")
    if pod_executor is None:
        pod_executor = K8sPodExecutor(
            **common,
            allowlist=ov.get("pod_allowlist"),
        )

    durable_policy = None
    if ov.get("egress_policy") is None or ov.get("egress_declaration") is None:
        durable_policy = DurableGrantEgressPolicy(
            grant_store,
            hop_inspector=ov.get("egress_hop_inspector"),
        )
    egress_policy = ov.get("egress_policy") or durable_policy
    declarations = ov.get("egress_declaration") or durable_policy
    egress_transport = ov.get("egress_transport")
    if egress_transport is None:
        egress_transport = K8sEgressTransport(
            **common,
            declarations=declarations,
        )

    for name, resource in (
        ("pod_executor", pod_executor),
        ("egress_transport", egress_transport),
    ):
        if not callable(getattr(resource, "execute", None)):
            raise PluginCompositionError(
                "capability_adapters", f"{name} must implement typed execute()"
            )
        if getattr(resource, "scoped_identity", False) is not True:
            raise PluginCompositionError(
                "capability_adapters",
                f"{name}: default mounted ServiceAccount is forbidden",
            )
        if getattr(resource, "digest_bound_channel", False) is not True:
            raise PluginCompositionError(
                "capability_adapters",
                f"{name}: digest-bound backend channel is required",
            )
        if not callable(getattr(resource, "aclose", None)):
            raise PluginCompositionError(
                "capability_adapters", f"{name} must support idempotent aclose()"
            )
    return pod_executor, egress_transport, egress_policy


def _validate_scoped_capability_transports(platform: Any) -> None:
    """Verify the concrete registered adapters, including explicit overrides."""
    try:
        pods_adapter = platform.registry.resolve("pods")
        network_adapter = platform.registry.resolve("network")
        pod_executor = pods_adapter._executor
        egress_transport = network_adapter._transport
    except Exception as exc:
        raise PluginCompositionError(
            "capability_adapters",
            "registered pods/network adapters do not expose their scoped runtimes",
        ) from exc
    for name, resource in (
        ("pod_executor", pod_executor),
        ("egress_transport", egress_transport),
    ):
        if (
            not callable(getattr(resource, "execute", None))
            or getattr(resource, "scoped_identity", False) is not True
            or getattr(resource, "digest_bound_channel", False) is not True
            or not callable(getattr(resource, "aclose", None))
        ):
            raise PluginCompositionError(
                "capability_adapters",
                f"registered {name} is not a scoped digest-bound runtime",
            )
    resolver = getattr(network_adapter, "_body_digest_resolver", None)
    if not callable(getattr(resolver, "resolve_body_digest", None)):
        raise PluginCompositionError(
            "capability_adapters",
            "enabled network adapter requires the singleton body digest resolver",
        )


def _secret_capability_adapter(
    *,
    repos: Any,
    ov: Mapping[str, Any],
    services: Mapping[str, Any] | None,
) -> Any | None:
    """Build digest reads with optional delivery; never construct a file store."""
    supplied = dict(ov.get("capability_adapters") or {})
    if ov.get("adapter_secrets") is not None or supplied.get("secrets") is not None:
        return None
    if ov.get("secret_store") is None:
        return None

    from prodavan.application.plugins.capabilities.adapters.secrets import (
        SecretStoreSecretsAdapter,
        SignedManifestSecretDeliveryPolicy,
    )

    projection = ov.get("secret_delivery_projection")
    if projection is None:
        candidate = ov.get("secret_projection")
        if candidate is None and services is not None:
            candidate = services.get("secret_projection")
        if not isinstance(candidate, _UnwiredPort):
            projection = candidate
    if not all(
        callable(getattr(projection, method, None))
        for method in ("deliver_secret", "revoke_secret")
    ):
        # Digest-only read remains wired; optional delivery is typed-unwired.
        projection = None

    policy = ov.get("secret_delivery_policy")
    if projection is not None and policy is None:
        registry = (services or {}).get("artifact_registry") or ov.get("artifact_registry")
        grants = ov.get("grant_store") or getattr(repos, "grant_store", None)
        if callable(getattr(registry, "get_async", None)) and callable(
            getattr(grants, "get_by_grant_id", None)
        ):
            policy = SignedManifestSecretDeliveryPolicy(
                artifact_registry=registry,
                grant_store=grants,
            )

    supplied_objects = (
        ov.get("adapter_objects") is not None or supplied.get("objects") is not None
    )
    owns_object_resources = not supplied_objects and bool(
        ov.get("own_object_resources", True)
    )
    return SecretStoreSecretsAdapter(
        store=ov.get("secret_store"),
        max_secret_bytes=ov.get("secret_max_bytes", 65536),
        owns_store=not owns_object_resources,
        projection=projection,
        delivery_policy=policy,
    )


def _event_integration_overrides(
    *, ov: Mapping[str, Any], repos: Any, runtime: Any,
    services: Mapping[str, Any] | None, settings: Any = None,
) -> dict[str, Any]:
    """Build the signed dispatcher handler/wake pair or fail typed-unwired."""

    from prodavan.application.plugins.events.dispatch import (
        ActivatingPluginEventHandler,
        HmacPluginEventSigner,
        SignedEventHandler,
    )
    from prodavan.application.plugins.events.errors import PluginEventError
    from prodavan.application.plugins.wiring.config import (
        PluginPlatformConfig,
        PluginPlatformConfigError,
        validate_event_signing_secret_ref,
    )

    raw_supplied = ov.get("capability_adapters") or {}
    if not isinstance(raw_supplied, Mapping):
        raise PluginCompositionError(
            "event_handler", "typed unwired: capability_adapters must be a mapping"
        )
    supplied = dict(raw_supplied)
    if ov.get("adapter_events") is not None or supplied.get("events") is not None:
        dispatcher = ov.get("event_dispatcher")
        if dispatcher is None:
            raise PluginCompositionError(
                "event_handler",
                "typed unwired: adapter_events override requires its real event_dispatcher",
            )
        return {"event_dispatcher": dispatcher}

    handler = ov.get("event_handler")
    wake_hook = ov.get("event_wake_hook")
    if (handler is None) != (wake_hook is None):
        raise PluginCompositionError(
            "event_handler",
            "typed unwired: event_handler and event_wake_hook must be configured together",
        )
    try:
        handler_signer = getattr(handler, "event_signer", None)
        signer = ov.get("event_signer") or handler_signer
        if (
            ov.get("event_signer") is not None
            and handler_signer is not None
            and ov.get("event_signer") is not handler_signer
        ):
            raise PluginCompositionError(
                "event_handler", "typed unwired: conflicting event signers"
            )
        if signer is None:
            secret_ref = ov.get("event_signing_secret_ref")
            if secret_ref is None and settings is not None:
                secret_ref = PluginPlatformConfig.from_settings(
                    settings
                ).event_signing_secret_ref
            # No direct-environment, claims-secret or development fallback.
            secret_ref = validate_event_signing_secret_ref(secret_ref, required=True)
            signer = HmacPluginEventSigner(
                secret_store=ov.get("secret_store"), secret_ref=secret_ref
            )
        if not all(callable(getattr(signer, method, None)) for method in ("sign", "verify")):
            raise PluginCompositionError(
                "event_handler", "typed unwired: event signer contract is invalid"
            )
        validate = getattr(signer, "validate", None)
        if callable(validate):
            validated = validate()
            if inspect.isawaitable(validated):
                raise PluginCompositionError(
                    "event_handler", "typed unwired: async signer validation is unsupported"
                )

        if handler is None:
            if not callable(getattr(signer, "signed_wire", None)):
                raise PluginCompositionError(
                    "event_handler", "typed unwired: signed_wire is required for backend delivery"
                )
            activator = (services or {}).get("activator") or ov.get("activator")
            delivery = ActivatingPluginEventHandler(
                activator=activator,
                proxy=getattr(runtime, "proxy", None),
                grant_store=repos.grant_store,
                signer=signer,
                timeout_seconds=float(ov.get("event_backend_timeout_seconds", 30.0)),
                wake_timeout=ov.get("event_wake_timeout_seconds"),
            )
            return {"event_handler": delivery, "event_wake_hook": delivery}

        if not callable(getattr(handler, "handle", None)):
            raise PluginCompositionError(
                "event_handler", "typed unwired: event handler must implement handle()"
            )
        if not callable(wake_hook) and not callable(getattr(wake_hook, "wake", None)):
            raise PluginCompositionError(
                "event_wake_hook", "typed unwired: wake hook must implement wake()"
            )
        if handler_signer is None:
            handler = SignedEventHandler(handler=handler, signer=signer)
        return {"event_handler": handler, "event_wake_hook": wake_hook}
    except PluginCompositionError:
        raise
    except PluginPlatformConfigError as exc:
        raise PluginCompositionError(
            "event_handler", f"typed unwired: {exc}"
        ) from None
    except PluginEventError as exc:
        raise PluginCompositionError(
            "event_handler", f"typed unwired: {exc.code.value}"
        ) from None
    except Exception as exc:
        raise PluginCompositionError(
            "event_handler", f"typed unwired: {type(exc).__name__}"
        ) from None


def _job_integration_override(
    *, ov: Mapping[str, Any], repos: Any, services: Mapping[str, Any] | None, redis: Any
) -> dict[str, Any]:
    """Build the manifest/digest resolver and concrete Celery queue adapter."""

    from prodavan.application.plugins.capabilities.adapters.jobs import CeleryJobsAdapter
    from prodavan.application.plugins.jobs.tasks import (
        JobTaskError,
        ManifestJobTasks,
        RedisJobExecutionStore,
    )
    from prodavan.core.infra.worker_manager import get_celery_app, get_worker_manager
    from prodavan.core.jobs.async_runner import run_async
    from prodavan.infrastructure.plugins.capabilities.redis import ManagerRedisClient

    raw_supplied = ov.get("capability_adapters") or {}
    if not isinstance(raw_supplied, Mapping):
        raise PluginCompositionError(
            "job_tasks", "typed unwired: capability_adapters must be a mapping"
        )
    supplied = dict(raw_supplied)
    if ov.get("adapter_jobs") is not None or supplied.get("jobs") is not None:
        return {}
    try:
        declarations = ov.get("job_tasks")
        if not isinstance(declarations, Mapping) or not declarations:
            raise PluginCompositionError(
                "job_tasks", "typed unwired: server-owned job_tasks mapping is required"
            )
        redis_client = ov.get("redis_client") or ManagerRedisClient(redis)
        execution_store = ov.get("job_execution_store") or RedisJobExecutionStore(
            redis=redis_client
        )
        runner = ov.get("job_async_runner") or run_async
        tasks = ManifestJobTasks(
            tasks=declarations,
            grant_store=repos.grant_store,
            artifact_registry=(services or {}).get("artifact_registry")
            or ov.get("artifact_registry"),
            execution_store=execution_store,
            async_runner=runner,
        )
        configured_app = ov.get("job_celery_app")
        registration_hook = ov.get("job_worker_registration")
        if (
            ov.get("job_queue") is None
            and configured_app is None
            and registration_hook is None
        ):
            raise PluginCompositionError(
                "job_tasks",
                "typed unwired: separate Celery worker registration hook is required",
            )
        if configured_app is not None:
            tasks.register(configured_app)
        if registration_hook is not None:
            if not callable(registration_hook):
                raise PluginCompositionError(
                    "job_tasks", "typed unwired: job_worker_registration must be callable"
                )
            registered = registration_hook(tasks)
            if inspect.isawaitable(registered):
                raise PluginCompositionError(
                    "job_tasks", "typed unwired: async worker registration is unsupported"
                )

        worker_provider = ov.get("job_worker_provider") or get_worker_manager
        celery_provider = ov.get("job_celery_provider") or get_celery_app

        def registered_worker() -> Any:
            app = celery_provider()
            tasks.register(app)
            return worker_provider()

        def registered_celery() -> Any:
            app = celery_provider()
            tasks.register(app)
            return app

        queue = ov.get("job_queue")
        if queue is None:
            from prodavan.infrastructure.plugins.capabilities.celery import (
                RedisCeleryJobQueue,
            )

            queue = RedisCeleryJobQueue(
                redis=redis_client,
                worker_provider=registered_worker,
                celery_provider=registered_celery,
            )
        return {
            "adapter_jobs": CeleryJobsAdapter(
                queue=queue,
                allowed_tasks=tasks,
                owns_queue=bool(ov.get("own_job_queue", False)),
                owns_tasks=True,
            )
        }
    except PluginCompositionError:
        raise
    except JobTaskError as exc:
        raise PluginCompositionError(
            "job_tasks", f"typed unwired: {exc.code}"
        ) from None
    except Exception as exc:
        raise PluginCompositionError(
            "job_tasks", f"typed unwired: {type(exc).__name__}"
        ) from None


def _platform_overrides(
    repos: Any, runtime: Any, admin_ports: Any, gateway_runtime: Any,
    admin_service: Any, ov: dict[str, Any], services: Mapping[str, Any] | None = None,
    settings: Any = None, redis: Any = None,
) -> dict[str, Any]:
    """Overrides шага (e) в ТОЧНЫХ именах ``PORT_OVERRIDES``/``SERVICE_OVERRIDES``.

    Один SQL-репозиторий грантов/квот/аудита/артефактов подключается во все
    зоны сразу (критерии №12/№22), а не разные объекты. Порты без прод-фабрики
    НЕ передаём вовсе: ``build_plugin_platform`` сам поставит свои fail-closed
    ``_Missing*Port``/``UnavailableAdapter`` и явно перечислит их в ``report()``.
    """

    out: dict[str, Any] = {
        # Обязательная тройка.
        "grant_store": repos.grant_store,
        "cosign": runtime.cosign,
        "admin_ports": admin_ports,
        # Durable-источник отзыва: DbGrantRevocation (is_active/revoke/is_revoked/check),
        # а НЕ «сырой» SqlCapabilityGrantRepository (у него другая форма deactivate).
        "revocation": repos.grant_revocation,
        # preferably SQL/Redis вместо InMemory*:
        "quota": repos.quota,
        "audit": repos.audit,
        "artifact_registry_store": repos.artifact_store,
        # ОДНА пара GatewayAuth/GatewayProxy и в platform.gateway, и в runtime.
        "gateway_auth": gateway_runtime.auth,
        "gateway_proxy": gateway_runtime.proxy,
        # Единый admin-сервис: и в platform.admin, и в app.state.
        "admin": admin_service,
    }
    if services is not None:
        # The admin/gateway pair and the platform must accept evidence sealed by
        # exactly the same verifier.  Passing only the SQL store here would make
        # build_plugin_platform construct a second verifier with another seal.
        for key in (
            "artifact_verifier", "artifact_registry", "tenant_db", "object_store",
            "activator", "object_buckets", "object_sts", "object_presigner",
            "tenant_db_secrets",
        ):
            value = services.get(key)
            if value is not None:
                out[key] = value
    # ``RuntimePorts.as_platform_overrides()``: cosign / activation_scale /
    # activation_readiness / activation_state_store / activation_proxy /
    # gateway_backend_http. У тестовых даблов метод может отсутствовать.
    if hasattr(runtime, "as_platform_overrides"):
        for key, value in _wrap("runtime_ports", runtime.as_platform_overrides).items():
            out.setdefault(key, value)
    from prodavan.application.plugins.capabilities.adapters.build import (
        build_capability_adapters,
    )

    pod_executor, egress_transport, egress_policy = _wrap(
        "capability_adapters", _capability_runtime_bindings, runtime, repos, ov, services
    )
    capability_overrides = dict(ov)
    event_bindings = _wrap(
        "event_handler",
        _event_integration_overrides,
        ov=capability_overrides,
        repos=repos,
        runtime=runtime,
        services=services,
        settings=settings,
    )
    capability_overrides.update(event_bindings)
    if "event_dispatcher" in event_bindings:
        out["event_dispatcher"] = event_bindings["event_dispatcher"]

    # One lifecycle-managed Redis wrapper and one claim registry are shared by
    # publish, per-subscription factories, raw DLQ and jobs. Explicit test or
    # standalone overrides are preserved without creating a second registry.
    from prodavan.infrastructure.plugins.adapters.redis_event_claims import (
        RedisDeliveryClaimStore,
    )
    from prodavan.infrastructure.plugins.capabilities.redis import ManagerRedisClient

    if ov.get("redis_client") is None and redis is None:
        raise PluginCompositionError(
            "event_delivery_claims",
            "shared lifecycle-managed Redis is required for delivery claims",
        )
    redis_client = ov.get("redis_client") or ManagerRedisClient(redis)
    # Normalize explicit None as absent: every downstream path must receive the
    # same wrapper, not independently construct A/B/C wrappers around redis.
    capability_overrides["redis_client"] = redis_client
    platform_claims = (ov.get("platform_overrides") or {}).get("event_delivery_claims")
    direct_claims = ov.get("event_delivery_claims")
    if direct_claims is not None and platform_claims is not None and direct_claims is not platform_claims:
        raise PluginCompositionError(
            "event_delivery_claims",
            "conflicting direct and platform delivery claim stores",
        )
    delivery_claims = direct_claims or platform_claims
    if delivery_claims is None:
        delivery_claims = RedisDeliveryClaimStore(
            redis=redis_client,
            prefix=ov.get(
                "event_delivery_claim_prefix",
                "agentscale:plugin:events:delivery-claims:",
            ),
        )
    capability_overrides["event_delivery_claims"] = delivery_claims
    out["event_delivery_claims"] = delivery_claims
    capability_overrides.update(
        _wrap(
            "job_tasks",
            _job_integration_override,
            ov=capability_overrides,
            repos=repos,
            services=services,
            redis=redis,
        )
    )
    capability_overrides["pod_executor"] = pod_executor
    capability_overrides["egress_transport"] = egress_transport
    capability_overrides["egress_policy"] = egress_policy
    secret_adapter = _wrap(
        "capability_adapters",
        _secret_capability_adapter,
        repos=repos,
        ov=capability_overrides,
        services=services,
    )
    if secret_adapter is not None:
        capability_overrides["adapter_secrets"] = secret_adapter
    resolver_factory = capability_overrides.get("body_digest_resolver_factory")
    if resolver_factory is not None:
        capability_overrides["body_digest_resolver"] = resolver_factory()
    supplied_network = capability_overrides.get("adapter_network") or (
        capability_overrides.get("capability_adapters") or {}
    ).get("network")
    if supplied_network is None:
        from prodavan.application.plugins.capabilities.adapters.network import (
            K8sEgressNetworkAdapter,
        )

        capability_overrides["adapter_network"] = K8sEgressNetworkAdapter(
            transport=egress_transport,
            egress=capability_overrides.get("egress_service") or egress_policy,
            initial_policy=egress_policy,
            body_digest_resolver=capability_overrides.get("body_digest_resolver"),
            owns_transport=bool(capability_overrides.get("own_egress_transport", True)),
        )
    out.update(
        _wrap(
            "capability_adapters",
            build_capability_adapters,
            redis=redis,
            repos=repos,
            runtime=runtime,
            services=services or {},
            settings=settings,
            overrides=capability_overrides,
        )
    )
    # Явные overrides родителя имеют приоритет, но НЕ могут обнулить порт.
    for key, value in dict(ov.get("platform_overrides") or {}).items():
        if value is None:
            raise PluginCompositionError(
                "platform", f"platform_overrides[{key!r}] is None; fail-closed"
            )
        if key == "event_delivery_claims" and value is not delivery_claims:
            raise PluginCompositionError(
                "event_delivery_claims",
                "platform override must use the shared delivery claim store",
            )
        out[key] = value

    final_dispatcher = out.get("event_dispatcher")
    if final_dispatcher is None:
        raise PluginCompositionError(
            "event_delivery_claims",
            "production event dispatcher is missing after composition",
        )
    if getattr(final_dispatcher, "_delivery_claims", None) is not delivery_claims:
        raise PluginCompositionError(
            "event_delivery_claims",
            "final event dispatcher ownership store differs from shared claims",
        )
    return out


def _mcp_discovery(platform: Any, repos: Any, ov: Mapping[str, Any]) -> Any:
    """Build discovery and its signed RPC backend broker on exact platform instances."""
    from prodavan.application.plugins.mcp.broker import DurableMcpBroker
    from prodavan.application.plugins.mcp.discovery import (
        ArtifactGrantMcpManifestSource,
        DurableMcpDiscovery,
    )

    adapter = platform.registry.resolve("mcp")
    store = ov.get("mcp_registration_store") or adapter.registration_store
    source = ov.get("mcp_manifest_source") or ArtifactGrantMcpManifestSource(
        grant_store=ov.get("mcp_grant_store") or repos.grant_store,
        artifact_registry=ov.get("mcp_artifact_registry") or platform.artifact_registry,
        timeout_seconds=float(ov.get("mcp_discovery_timeout_seconds", 3.0)),
    )
    consumer = DurableMcpDiscovery(
        store=store,
        manifest_source=source,
        gateway=ov.get("mcp_gateway") or platform.capability_service,
        max_arguments_bytes=int(ov.get("mcp_max_arguments_bytes", 64 * 1024)),
        max_response_bytes=int(ov.get("mcp_max_response_bytes", 64 * 1024)),
        max_tools=int(ov.get("mcp_max_tools", 64)),
        timeout_seconds=float(ov.get("mcp_discovery_timeout_seconds", 3.0)),
    )
    broker = ov.get("mcp_backend_broker") or DurableMcpBroker(
        discovery=consumer,
        manifest_source=source,
        grant_store=ov.get("mcp_grant_store") or repos.grant_store,
        backend_proxy=ov.get("mcp_backend_proxy") or platform.gateway_proxy,
        max_arguments_bytes=int(ov.get("mcp_max_arguments_bytes", 64 * 1024)),
        max_response_bytes=int(ov.get("mcp_max_response_bytes", 64 * 1024)),
    )
    adapter.attach_broker(broker)
    return consumer


# ---------------------------------------------------------------------------
# Главная точка входа (прод-контур).
# ---------------------------------------------------------------------------


def install_plugin_platform(
    app: Any,
    settings: Any,
    *,
    session: Any = None,
    redis: Any = None,
    overrides: Mapping[str, Any] | None = None,
) -> Any | None:
    """Собрать и привязать ВСЮ plugin-платформу ОДНИМ вызовом.

    Родитель пишет в ``main.py`` ровно одну строку::

        install_plugin_platform(app, settings, session=session, redis=redis)

    ``enabled=False`` -> ``None``, ``app.state`` НЕ тронут (роутеры отдают 503).
    ``enabled=True`` -> полный порядок a..i, любая ошибка = ``PluginCompositionError``
    с именем шага + полный откат ``app.state``.

    ``overrides`` — НЕобязательная явная инъекция ЛЮБОГО адаптера/сервиса
    (``repositories``, ``runtime_ports``, ``admin_ports``, ``admin_service``,
    ``gateway_runtime``, ``platform``, порты зон, ``platform_overrides``,
    ``repository_kwargs``, ``runtime_kwargs``). Без overrides модуль собирает
    всё из реальных фабрик worktree, а порты без фабрики помечает в
    ``app.state.plugin_unwired_ports``. ``enabled`` читается ТОЛЬКО из
    ``settings`` через ``PluginPlatformConfig``.
    """

    ov: dict[str, Any] = dict(overrides or {})
    config = _wrap("config", _cfg, settings)
    if not config.enabled:
        # Ни одного сервиса, ни одного ключа app.state, ни одного импорта чужих
        # build-модулей: выключено -> роутеры честно отдают 503.
        logger.info("plugin platform disabled; install_plugin_platform is a no-op")
        return None

    # Шаг (a): БД/Redis обязательны ДО любого build (in-memory подмена
    # источника истины в проде ЗАПРЕЩЕНА, A10).
    if session is None or redis is None:
        missing = [n for n, v in (("session", session), ("redis", redis)) if v is None]
        raise PluginCompositionError(
            "repositories",
            "enabled=True требует " + " и ".join(missing)
            + "; in-memory подмена БД/Redis в проде запрещена (A10)",
        )

    composer = _Composer(app)
    unwired: list[str] = []
    in_memory: list[str] = []
    runtime: Any = None
    platform: Any = None
    payload_store: Any = None
    mcp_discovery: Any = None
    # ОДНИ и те же реальные сервисы зон использует и admin (шаг c), и gateway
    # (шаг d), и platform (шаг e): иначе capability-запросы, отзыв и wake
    # смотрели бы в разные объекты зон.
    zone_services: dict[str, Any] = {}
    try:
        # One control-plane store is resolved once, never in request handlers.
        # The same object goes to objects, secrets, Kafka and MinIO; no runtime backend.
        ov["secret_store"] = _wrap("secret_store", _shared_secret_store, ov)

        # --- i) guard ДО attach (решение по transport/guard.py) -------------
        _register_guard_once(app, composer)

        # --- a) SQL/Redis-репозитории (A10) --------------------------------
        repos = ov.get("repositories")
        if repos is None:
            build_repos = _load(
                "repositories",
                "prodavan.infrastructure.persistence.repositories.plugins.build",
                "build_plugin_repositories",
            )
            repos = _wrap(
                "repositories", build_repos, session, redis,
                **dict(ov.get("repository_kwargs") or {}),
            )
        if repos is None:
            raise PluginCompositionError("repositories", "build_plugin_repositories вернул None")

        # Build before capability adapters so the network owner can inject this
        # exact factory without a second Redis-backed store or app.state lookup.
        payload_store = _wrap(
            "payload_store", _publish_digest_payload_store, redis, ov, composer
        )
        ov["digest_payload_store"] = payload_store
        ov["body_digest_resolver_factory"] = getattr(
            composer._state, BODY_DIGEST_RESOLVER_FACTORY_STATE_KEY
        )

        # --- b) runtime-порты: cosign/k8s/state/proxy ----------------------
        runtime = ov.get("runtime_ports")
        if runtime is None:
            build_runtime = _load(
                "runtime_ports",
                "prodavan.infrastructure.plugins.runtime.build",
                "build_runtime_ports",
            )
            runtime_kwargs = dict(ov.get("runtime_kwargs") or {})
            if (
                "instance_store" not in runtime_kwargs
                and "resource_name_resolver" not in runtime_kwargs
                and (ov.get("instance_store") or getattr(repos, "instance_store", None))
                is not None
            ):
                # Async durable lookup restores canonical names after restart.
                runtime_kwargs["instance_store"] = (
                    ov.get("instance_store") or repos.instance_store
                )
            runtime = _wrap(
                "runtime_ports", build_runtime, settings,
                **runtime_kwargs,
            )
        if runtime is None:
            raise PluginCompositionError("runtime_ports", "build_runtime_ports вернул None")

        # One real admin adapter for the whole app; lifespan opens/closes it.
        if ov.get("admin_ports") is None and ov.get("tenant_db_admin") is None:
            tenant_db_admin = _wrap("tenant_db_admin", _tenant_db_admin, settings)
            if tenant_db_admin is not None:
                ov["tenant_db_admin"] = tenant_db_admin

        # --- c) PluginAdminPorts (12 полей) + PluginAdminService ------------
        admin_ports = ov.get("admin_ports") or _wrap(
            "admin_ports", _admin_ports, runtime, repos, config, ov, unwired,
            zone_services, in_memory, settings=settings,
        )
        admin_service = ov.get("admin_service")
        if admin_service is None:
            from prodavan.application.plugins.admin.service import PluginAdminService

            admin_service = _wrap("admin_ports", PluginAdminService, admin_ports)
        if admin_ports is None or admin_service is None:
            raise PluginCompositionError("admin_ports", "admin runtime не собран")

        # --- d) PluginGatewayRuntime ----------------------------------------
        gateway_runtime = ov.get("gateway_runtime") or _wrap(
            "gateway_runtime", _gateway_runtime, runtime, repos, redis, config,
            ov, unwired, zone_services,
        )
        if gateway_runtime is None:
            raise PluginCompositionError("gateway_runtime", "build_gateway_runtime вернул None")

        if unwired:
            raise PluginCompositionError(
                "required_ports",
                "enabled platform forbids _UnwiredPort on required paths: "
                + ", ".join(sorted(set(unwired))),
            )

        # --- e) build_plugin_platform ---------------------------------------
        platform = ov.get("platform")
        if platform is None:
            platform_overrides = _wrap(
                "platform", _platform_overrides,
                repos, runtime, admin_ports, gateway_runtime, admin_service, ov,
                zone_services, settings, redis,
            )
            build_platform = _load(
                "platform",
                "prodavan.application.plugins.wiring.platform",
                "build_plugin_platform",
            )
            platform = _wrap("platform", build_platform, settings, **platform_overrides)
        if platform is None:
            raise PluginCompositionError(
                "platform", "build_plugin_platform вернул None при enabled=True"
            )
        if zone_services:
            if platform.artifact_verifier is not zone_services.get("artifact_verifier"):
                raise PluginCompositionError(
                    "platform", "artifact verifier identity differs from admin/gateway services"
                )
            if platform.artifact_registry is not zone_services.get("artifact_registry"):
                raise PluginCompositionError(
                    "platform", "artifact registry identity differs from admin/gateway services"
                )

        mcp_discovery = ov.get("mcp_discovery") or _wrap(
            "mcp_discovery", _mcp_discovery, platform, repos, ov
        )
        attach_discovery = _load(
            "mcp_discovery",
            "prodavan.application.plugins.wiring.adapters",
            "attach_mcp_discovery",
        )
        _wrap(
            "mcp_discovery", attach_discovery, platform.registry, mcp_discovery
        )

        # --- f) attach_plugin_platform -> STATE_KEYS wiring (19) -------------
        attach = _load(
            "attach_platform",
            "prodavan.application.plugins.wiring.state",
            "attach_plugin_platform",
        )
        from prodavan.application.plugins.wiring.state import STATE_KEYS  # лениво

        _wrap("attach_platform", attach, app, platform)
        composer.mark(*STATE_KEYS)
        setattr(composer._state, MCP_DISCOVERY_STATE_KEY, mcp_discovery)
        composer.mark(MCP_DISCOVERY_STATE_KEY)

        # --- g) install_plugin_gateway_runtime --------------------------------
        install_runtime = _load(
            "gateway_install",
            "prodavan.api.v1.plugin_gateway",
            "install_plugin_gateway_runtime",
        )
        _wrap("gateway_install", install_runtime, app, gateway_runtime)
        composer.mark("plugin_gateway_runtime")

        # --- h) attach_admin_runtime ------------------------------------------
        attach_admin = _load(
            "admin_attach",
            "prodavan.application.plugins.platform_admin.state",
            "attach_admin_runtime",
        )
        _wrap("admin_attach", attach_admin, app, ports=admin_ports, service=admin_service)
        composer.mark("plugin_admin_ports", "plugin_admin_service")

        # Диагностика долгов F11 (не «тихий успех»): что ещё требует реального
        # адаптера A10/14/15. В списке только имена портов, без секретов.
        # in-memory порты ПРИВЯЗАНЫ, поэтому идут в отдельный ключ: иначе
        # Verify Dev видел бы ложный «не подключено» вместо честного «нужна БД».
        # Обнаружение идёт по фактическому классу привязанного порта, а не по
        # внешнему флагу: раньше ничто не наполняло список, и ключ всегда был
        # пустым, из-за чего проверки композиции проходили вхолостую.
        in_memory.extend(_in_memory_port_names({
            "grant_store": repos.grant_store,
            "quota": repos.quota,
            "audit": repos.audit,
            "artifact_store": repos.artifact_store,
            "revocation": repos.grant_revocation,
            "routes": ov.get("routes"),
        }))
        setattr(composer._state, UNWIRED_PORTS_STATE_KEY, tuple(sorted(set(unwired))))
        composer.mark(UNWIRED_PORTS_STATE_KEY)
        setattr(composer._state, IN_MEMORY_PORTS_STATE_KEY, tuple(sorted(set(in_memory))))
        composer.mark(IN_MEMORY_PORTS_STATE_KEY)

        _register_digest_payload_shutdown(app, payload_store, composer, overrides=ov)

        # Runtime shutdown is attached only after every build/attach step succeeds.
        # RuntimePorts.aclose() is idempotent, so repeated lifespan signals close once.
        _register_runtime_shutdown(app, runtime, composer, overrides=ov)
        _register_tenant_db_admin_lifecycle(
            app,
            _TenantDbAdminLifecycle(ov.get("tenant_db_admin")),
            composer,
            overrides=ov,
        )
        object_sts = zone_services.get("object_sts") or ov.get("object_sts")
        if object_sts is None and bool(
            getattr(ov.get("minio_config"), "sts_probe_enabled", False)
        ):
            raise PluginCompositionError(
                "minio_sts_probe", "probe is enabled but the shared MinioSts adapter is absent",
            )
        _register_minio_sts_lifecycle(app, object_sts, composer, overrides=ov)
        # Capability shutdown registers BEFORE the final app.state verify: its
        # lifecycle key is part of _OWN_STATE_KEYS. The position right after
        # minio keeps the same manager registration order (payload, runtime,
        # tenant, minio, capability) and the same reverse shutdown order.
        _register_capability_shutdown(app, platform, composer, overrides=ov)

        # Финальная сверка: ни одного «почти привязанного» ключа.
        for key in _OWN_STATE_KEYS:
            if not hasattr(composer._state, key):
                raise PluginCompositionError(
                    "verify", f"app.state.{key} отсутствует после композиции"
                )
        adapter_report = platform.report().get("adapters", {})
        if adapter_report.get("stub_count"):
            raise PluginCompositionError(
                "verify", "enabled platform contains UnavailableAdapter"
            )
        from prodavan.application.plugins.capabilities.contracts import CapabilityName

        expected_capabilities = {item.value for item in CapabilityName}
        if set(adapter_report.get("registered", ())) != expected_capabilities:
            raise PluginCompositionError(
                "verify", "enabled platform registry does not contain all capabilities"
            )
        for capability in expected_capabilities:
            if not callable(getattr(platform.registry.resolve(capability), "quota_usage", None)):
                raise PluginCompositionError(
                    "verify", f"capability {capability} has no quota_usage hook"
                )
        _wrap(
            "capability_adapters", _validate_scoped_capability_transports, platform
        )
        logger.info("plugin platform installed: all app.state keys bound, guard once")
        return platform
    except Exception:
        composer.rollback()
        _close_runtime_after_failed_build(runtime)
        if platform is not None and platform is not runtime:
            _close_runtime_after_failed_build(platform)
        if payload_store is not None:
            _close_runtime_after_failed_build(payload_store)
        if mcp_discovery is not None:
            _close_runtime_after_failed_build(mcp_discovery)
        raise


# ---------------------------------------------------------------------------
# Emergency-disabled GitHub Actions CI publication (no install/deploy wiring).
# ---------------------------------------------------------------------------


def plugin_ci_publication_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Read the independent CI flag strictly; an invalid value fails closed."""
    source = os.environ if environ is None else environ
    raw = str(source.get(PLUGIN_CI_PUBLICATION_ENABLED_ENV, "") or "").strip().lower()
    if not raw or raw in {"0", "false", "no", "off", "disabled"}:
        return False
    if raw in {"1", "true", "yes", "on", "enabled"}:
        return True
    raise PluginCompositionError(
        "ci_publication_config",
        f"{PLUGIN_CI_PUBLICATION_ENABLED_ENV} must be an explicit boolean",
    )


def _ci_required_env(source: Mapping[str, str], name: str, *, step: str) -> str:
    value = source.get(name)
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(character.isspace() for character in value)
    ):
        raise PluginCompositionError(
            step,
            f"{name} is required when {PLUGIN_CI_PUBLICATION_ENABLED_ENV}=true; "
            "no anonymous or cross-auth fallback is allowed",
        )
    return value


class _PluginCiPublicationLifecycle:
    """Close only resources created by the standalone CI composition."""

    def __init__(
        self,
        *,
        artifact_store: Any = None,
        registry_authorizer: Any = None,
    ) -> None:
        self._artifact_store = artifact_store
        self._registry_authorizer = registry_authorizer

    @property
    def name(self) -> str:
        return _PLUGIN_CI_LIFESPAN_NAME

    async def startup(self) -> None:
        # SQL sessions and the sync bridge loop are lazy. GitHub App is a
        # borrowed provider owned by the source composition, not by this resource.
        return None

    async def shutdown(self) -> None:
        try:
            if self._registry_authorizer is not None:
                close_authorizer = getattr(self._registry_authorizer, "aclose", None)
                if callable(close_authorizer):
                    await close_authorizer()
        finally:
            close = getattr(self._artifact_store, "close", None)
            if callable(close):
                await asyncio.to_thread(close)

    async def health(self) -> None:
        return None


def _close_ci_store_after_failed_build(store: Any) -> None:
    close = getattr(store, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:  # noqa: BLE001 — rollback must preserve the startup error
        logger.warning("CI publication rollback could not close its artifact store")


def install_plugin_ci_publication(
    app: Any,
    settings: Any,
    *,
    session_factory: Any = None,
    lifespan_manager: Any = None,
    managed_organization: Any = None,
    github_app: Any = None,
    source_config: Any = None,
    approval_reader: Any = None,
    environ: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Any | None:
    """Build one CI publication service, without enabling install/deploy.

    Enabled composition requires a dedicated GitHub OIDC audience, a trusted
    ManagedOrganization plus GitHubAppClient for registry_read tokens, a durable
    PublicationApproval reader, the existing cosign identity, and the shared SQL
    session factory. Source, approval and artifact repositories use that exact
    factory. There is no static-token or ACTIVE-grant fallback, anonymous GHCR
    loader, Keycloak/employee validator, in-memory store, installer, deployment
    admin, grant writer call, or plugin-platform side effect.

    If the full platform is already attached, its verifier/registry pair is
    reused.  Otherwise the CI contour creates that pair once and owns its SQL
    artifact store through the supplied lifespan manager. The separate source
    composition owns the request-service factory and GitHub App lifecycle. Any
    failure restores CI-owned app.state values and closes only an owned store;
    the borrowed provider and source factory are left untouched.
    """
    source = os.environ if environ is None else environ
    if not plugin_ci_publication_enabled(source):
        return None

    from prodavan.api.internal.plugin_artifacts import (
        PLUGIN_CI_PUBLICATION_SERVICE_STATE,
    )
    from prodavan.application.plugins.artifacts.oidc import GitHubActionsOidcValidator
    from prodavan.application.plugins.artifacts.publication import (
        DurablePluginInstanceScopeResolver,
        HttpxOciArtifactLoader,
        PluginCiPublicationService,
    )
    from prodavan.application.plugins.artifacts.registry import PluginArtifactRegistry
    from prodavan.application.plugins.artifacts.verification import (
        PLATFORM_VERSION,
        ArtifactVerifier,
        CompatibilityGate,
        CosignPort,
    )
    from prodavan.application.plugins.sources.repository import SqlModuleSourceRepository
    from prodavan.application.plugins.sources.service import ModuleSourceProvisioningConfig
    from prodavan.infrastructure.persistence.repositories.plugins.artifacts import (
        SqlArtifactRegistryStore,
    )
    from prodavan.infrastructure.persistence.repositories.plugins.grants import (
        SqlCapabilityGrantRepository,
    )
    from prodavan.infrastructure.persistence.repositories.plugins.instances import (
        SqlPluginInstanceRepository,
    )
    from prodavan.infrastructure.persistence.repositories.plugins.publication_approvals import (
        SqlPluginPublicationApprovalRepository,
    )
    from prodavan.infrastructure.plugins.artifacts.ghcr_auth import (
        GhcrRegistryAuthorizer,
    )
    from prodavan.infrastructure.plugins.sources.github_app import GitHubAppClient
    from prodavan.infrastructure.plugins.sources.models import (
        ManagedOrganization,
        validate_branch,
    )

    state = getattr(app, "state", None)
    if state is None:
        raise PluginCompositionError("ci_state", "app has no .state")
    existing_service = getattr(state, PLUGIN_CI_PUBLICATION_SERVICE_STATE, None)
    if existing_service is not None:
        if not isinstance(existing_service, PluginCiPublicationService):
            raise PluginCompositionError(
                "ci_state",
                f"app.state.{PLUGIN_CI_PUBLICATION_SERVICE_STATE} has an unexpected type",
            )
        return existing_service
    if session_factory is None:
        raise PluginCompositionError(
            "ci_repositories",
            "enabled CI publication requires get_session_factory(); "
            "a request-scoped or in-memory replacement is forbidden",
        )
    if lifespan_manager is None or not callable(getattr(lifespan_manager, "register", None)):
        raise PluginCompositionError(
            "ci_lifecycle",
            "enabled CI publication requires the application lifespan manager",
        )

    ov: dict[str, Any] = dict(overrides or {})
    managed_organization = managed_organization or ov.get("managed_organization")
    github_app = github_app or ov.get("github_app")
    source_config = source_config or ov.get("source_config")
    approval_reader = approval_reader or ov.get("approval_reader")
    state_verifier = getattr(state, "plugin_artifact_verifier", None)
    state_registry = getattr(state, "plugin_artifact_registry", None)
    owned_store: Any = None
    owned_authorizer: Any = None
    state_touched = False
    keys = (
        "plugin_artifact_verifier",
        "plugin_artifact_registry",
        PLUGIN_CI_PUBLICATION_SERVICE_STATE,
    )
    before = {
        key: (hasattr(state, key), getattr(state, key, None))
        for key in keys
    }

    try:
        # Publication consumes durable exact pre-approvals, never an ACTIVE
        # CapabilityGrant. The concrete SQL reader shares the process factory;
        # no compatibility adapter or in-memory fallback is accepted.
        if approval_reader is None:
            approval_reader = SqlPluginPublicationApprovalRepository(
                session_factory=session_factory
            )
        if not isinstance(approval_reader, SqlPluginPublicationApprovalRepository):
            raise PluginCompositionError(
                "ci_approval",
                "CI publication requires SqlPluginPublicationApprovalRepository; "
                "ACTIVE grant and in-memory fallbacks are forbidden",
            )
        if getattr(approval_reader, "_session_factory", None) is not session_factory:
            raise PluginCompositionError(
                "ci_approval",
                "approval reader does not use the shared process session factory",
            )

        # Dedicated OIDC is built before any repository.  In particular, do not
        # pass get_jwt_validator()/Keycloak settings into this trust contour.
        oidc_validator = ov.get("oidc_validator")
        if oidc_validator is None:
            audience = _ci_required_env(
                source, PLUGIN_CI_OIDC_AUDIENCE_ENV, step="ci_oidc"
            )
            oidc_validator = _wrap(
                "ci_oidc", GitHubActionsOidcValidator, audience=audience
            )
        if not callable(getattr(oidc_validator, "validate_for_source", None)):
            raise PluginCompositionError(
                "ci_oidc", "oidc_validator must implement validate_for_source()"
            )

        artifact_loader = ov.get("artifact_loader")
        registry_authorizer = ov.get("registry_authorizer")
        if artifact_loader is None:
            if managed_organization is None:
                raise PluginCompositionError(
                    "ci_registry_auth",
                    "GHCR registry_read requires ManagedOrganization configuration "
                    "for the GitHub App installation",
                )
            if not isinstance(managed_organization, ManagedOrganization):
                raise PluginCompositionError(
                    "ci_registry_auth",
                    "managed_organization must be ManagedOrganization; installation_id "
                    "is never request input",
                )
            if github_app is None:
                raise PluginCompositionError(
                    "ci_registry_auth",
                    "GHCR registry_read requires the shared GitHubAppClient "
                    "installation-token provider",
                )
            if not isinstance(github_app, GitHubAppClient):
                raise PluginCompositionError(
                    "ci_registry_auth",
                    "github_app must be the real GitHubAppClient, not a static "
                    "token or fake production provider",
                )
            if not callable(getattr(github_app, "installation_token", None)):
                raise PluginCompositionError(
                    "ci_registry_auth",
                    "github_app must expose the shared installation_token() delegate",
                )
            provider_organization = _wrap(
                "ci_registry_auth", github_app._organization, managed_organization.name
            )
            if provider_organization != managed_organization:
                raise PluginCompositionError(
                    "ci_registry_auth",
                    "GitHubAppClient managed organization/installation binding differs "
                    "from the GHCR configuration",
                )
            if registry_authorizer is None:
                registry_authorizer = _wrap(
                    "ci_registry_auth",
                    GhcrRegistryAuthorizer,
                    organization=managed_organization.name,
                    installation_id=managed_organization.installation_id,
                    token_provider=github_app,
                )
                owned_authorizer = registry_authorizer
            if not callable(getattr(registry_authorizer, "authorization_for", None)):
                raise PluginCompositionError(
                    "ci_registry_auth",
                    "registry_authorizer must implement authorization_for()",
                )
            artifact_loader = _wrap(
                "ci_loader",
                HttpxOciArtifactLoader,
                allowed_registries=("ghcr.io",),
                authorizer=registry_authorizer,
            )
        for method in ("load_manifest", "load_blob"):
            if not callable(getattr(artifact_loader, method, None)):
                raise PluginCompositionError(
                    "ci_loader", f"artifact_loader must implement {method}()"
                )

        if not isinstance(source_config, ModuleSourceProvisioningConfig):
            raise PluginCompositionError(
                "ci_source_config",
                "source provisioning requires ModuleSourceProvisioningConfig; "
                "the mounted source API stays fail-closed without it",
            )
        if source_config.organization is not managed_organization:
            raise PluginCompositionError(
                "ci_source_config",
                "source config and GHCR must use the identical ManagedOrganization",
            )
        _wrap("ci_source_config", source_config.trust.validate)
        _wrap("ci_source_config", validate_branch, source_config.default_branch)

        identity = getattr(settings, "plugin_cosign_identity", "")
        issuer = getattr(settings, "plugin_cosign_issuer", "")
        if not isinstance(identity, str) or not identity.strip():
            raise PluginCompositionError(
                "ci_verifier", "PLUGIN_COSIGN_IDENTITY is required for CI publication"
            )
        if not isinstance(issuer, str) or not issuer.strip():
            raise PluginCompositionError(
                "ci_verifier", "PLUGIN_COSIGN_ISSUER is required for CI publication"
            )

        sources = ov.get("source_repository")
        if sources is None:
            sources = SqlModuleSourceRepository(session_factory=session_factory)
        if not isinstance(sources, SqlModuleSourceRepository):
            raise PluginCompositionError(
                "ci_repositories", "CI publication requires SqlModuleSourceRepository"
            )

        instance_scope_resolver = ov.get("instance_scope_resolver")
        scope_grants = ov.get("instance_scope_grant_store")
        scope_instances = ov.get("instance_scope_instance_store")
        if instance_scope_resolver is not None:
            if not isinstance(
                instance_scope_resolver,
                DurablePluginInstanceScopeResolver,
            ):
                raise PluginCompositionError(
                    "ci_instance_scope",
                    "CI publication requires the durable SQL scope resolver; "
                    "an in-memory fallback is forbidden",
                )
            if scope_grants is None:
                scope_grants = instance_scope_resolver._grant_store
            if scope_instances is None:
                scope_instances = instance_scope_resolver._instance_store
            if (
                scope_grants is not instance_scope_resolver._grant_store
                or scope_instances is not instance_scope_resolver._instance_store
            ):
                raise PluginCompositionError(
                    "ci_instance_scope",
                    "resolver and scope repositories must be the identical objects",
                )
        else:
            # Reuse the attached platform's exact repositories when available.
            # Standalone CI builds the same SQL ports on the one process factory.
            admin_ports = getattr(state, "plugin_admin_ports", None)
            if scope_grants is None:
                scope_grants = getattr(admin_ports, "grant_store", None)
            if scope_instances is None:
                scope_instances = getattr(admin_ports, "instance_store", None)
        if scope_grants is None:
            scope_grants = SqlCapabilityGrantRepository(
                session_factory=session_factory
            )
        if scope_instances is None:
            scope_instances = SqlPluginInstanceRepository(
                session_factory=session_factory
            )
        for label, repository, expected_type in (
            ("grant", scope_grants, SqlCapabilityGrantRepository),
            ("instance", scope_instances, SqlPluginInstanceRepository),
        ):
            if not isinstance(repository, expected_type):
                raise PluginCompositionError(
                    "ci_instance_scope",
                    f"{label} scope repository must be durable SQL; "
                    "an in-memory fallback is forbidden",
                )
            if (
                getattr(repository, "_session_factory", None) is not session_factory
                or getattr(repository, "_session", None) is not None
            ):
                raise PluginCompositionError(
                    "ci_instance_scope",
                    f"{label} scope repository does not use the shared process "
                    "session factory",
                )
        if instance_scope_resolver is None:
            instance_scope_resolver = DurablePluginInstanceScopeResolver(
                grant_store=scope_grants,
                instance_store=scope_instances,
            )

        verifier = ov.get("artifact_verifier") or state_verifier
        registry = ov.get("artifact_registry") or state_registry
        if verifier is None and registry is not None:
            verifier = getattr(registry, "_verifier", None)
        if verifier is None:
            cosign = ov.get("cosign")
            if cosign is None:
                from prodavan.infrastructure.plugins.runtime.cosign_adapter import (
                    CliCosignPort,
                )

                timeout_raw = str(source.get("PLUGIN_RUNTIME_COSIGN_TIMEOUT", "60.0"))
                try:
                    timeout = float(timeout_raw)
                except ValueError as exc:
                    raise PluginCompositionError(
                        "ci_verifier", "PLUGIN_RUNTIME_COSIGN_TIMEOUT must be numeric"
                    ) from exc
                cosign = _wrap(
                    "ci_verifier",
                    CliCosignPort,
                    binary=str(source.get("PLUGIN_RUNTIME_COSIGN_BIN", "cosign")),
                    timeout_seconds=timeout,
                    kms_key_ref=str(source.get("PLUGIN_RUNTIME_KMS_KEY_REF", "") or "") or None,
                    runner=ov.get("cosign_runner"),
                    attestation_verifier=ov.get("attestation_verifier"),
                )
            if not isinstance(cosign, CosignPort):
                raise PluginCompositionError(
                    "ci_verifier", "cosign must implement CosignPort"
                )
            verifier = _wrap(
                "ci_verifier",
                ArtifactVerifier,
                cosign,
                validator=ov.get("manifest_validator"),
                identity=identity,
                issuer=issuer,
                compatibility=CompatibilityGate(platform_version=PLATFORM_VERSION),
                clock=ov.get("clock"),
            )
        if not isinstance(verifier, ArtifactVerifier):
            raise PluginCompositionError(
                "ci_verifier", "artifact_verifier must be ArtifactVerifier"
            )

        if registry is None:
            artifact_store = ov.get("artifact_store")
            if artifact_store is None:
                artifact_store = SqlArtifactRegistryStore(
                    session_factory=session_factory
                )
                owned_store = artifact_store
            registry = _wrap(
                "ci_registry",
                PluginArtifactRegistry,
                store=artifact_store,
                verifier=verifier,
                clock=ov.get("clock"),
            )
        if not isinstance(registry, PluginArtifactRegistry):
            raise PluginCompositionError(
                "ci_registry", "artifact_registry must be PluginArtifactRegistry"
            )
        if getattr(registry, "_verifier", None) is not verifier:
            raise PluginCompositionError(
                "ci_verifier",
                "ArtifactVerifier and PluginArtifactRegistry have different evidence seals",
            )
        if state_verifier is not None and state_verifier is not verifier:
            raise PluginCompositionError(
                "ci_verifier", "refusing to replace the attached platform verifier"
            )
        if state_registry is not None and state_registry is not registry:
            raise PluginCompositionError(
                "ci_registry", "refusing to replace the attached platform registry"
            )

        artifact_store = getattr(registry, "_store", None)
        if not isinstance(artifact_store, SqlArtifactRegistryStore):
            raise PluginCompositionError(
                "ci_repositories",
                "CI publication requires durable SqlArtifactRegistryStore; "
                "an in-memory production fallback is forbidden",
            )
        for label, repository in (
            ("source", sources),
            ("approval", approval_reader),
            ("artifact", artifact_store),
        ):
            if (
                getattr(repository, "_session_factory", None) is not session_factory
                or getattr(repository, "_session", None) is not None
            ):
                raise PluginCompositionError(
                    "ci_repositories",
                    f"{label} store does not use the shared process session factory",
                )

        service = PluginCiPublicationService(
            source_repository=sources,
            oidc_validator=oidc_validator,
            artifact_loader=artifact_loader,
            verifier=verifier,
            artifact_registry=registry,
            approval_reader=approval_reader,
            instance_scope_resolver=instance_scope_resolver,
            manifest_validator=ov.get("manifest_validator"),
        )

        state_touched = True
        setattr(state, "plugin_artifact_verifier", verifier)
        setattr(state, "plugin_artifact_registry", registry)
        setattr(state, PLUGIN_CI_PUBLICATION_SERVICE_STATE, service)
        if owned_store is not None or owned_authorizer is not None:
            _wrap(
                "ci_lifecycle",
                lifespan_manager.register,
                _PluginCiPublicationLifecycle(
                    artifact_store=owned_store,
                    registry_authorizer=owned_authorizer,
                ),
            )
        return service
    except Exception as exc:
        if state_touched:
            for key in reversed(keys):
                existed, old_value = before[key]
                if existed:
                    setattr(state, key, old_value)
                else:
                    try:
                        delattr(state, key)
                    except AttributeError:
                        pass
        if owned_store is not None:
            _close_ci_store_after_failed_build(owned_store)
        if isinstance(exc, PluginCompositionError):
            raise
        raise PluginCompositionError(
            "ci_publication", f"composition failed with {type(exc).__name__}"
        ) from exc


# ---------------------------------------------------------------------------
# Вариант для unit/acceptance-тестов: in-memory fakes, БЕЗ БД/Redis/k8s/cosign.
# ---------------------------------------------------------------------------


def install_plugin_platform_for_tests(
    app: Any,
    settings: Any,
    **fakes: Any,
) -> Any | None:
    """Вариант ``install_plugin_platform`` с in-memory фейками (тесты/dev).

    Идеологически переиспользует фейки из
    ``apps/api/tests/acceptance/plugins/conftest.py`` (``FakeGrantStore``,
    ``FakeCosign``, ``FakeRevocation``, ``FakeGatewaySessions`` …), но НЕ
    импортирует тесты в прод-код: все объекты приходят через ``**fakes``.
    Никаких БД/Redis/k8s/cosign CLI.

    Ожидаемые ключи ``fakes`` при ``enabled=True``: ``grant_store``, ``cosign``,
    ``admin_ports``, ``gateway_runtime``, ``admin_service`` (или соберётся из
    ``admin_ports``), ``platform`` (или соберётся через ``build_plugin_platform``),
    ``runtime_ports`` (объект с ``as_platform_overrides()``), плюс любые
    ``platform_overrides`` для портов зон. Guard регистрируется ровно один раз,
    ошибка любого шага -> ``PluginCompositionError`` + полный откат ``app.state``.

    ``enabled`` читается структурно (``settings.plugin_platform_enabled``), БЕЗ
    валидации секретов: тесты/dev вправе передать минимальный объект настроек.
    ``enabled=False`` -> ``None`` без единого импорта чужих модулей.
    """

    # Структурный只读: НЕ вызываем PluginPlatformConfig.from_settings (он
    # валидирует секрет/origin/identity, которых у тестового settings может не быть).
    if not bool(getattr(settings, "plugin_platform_enabled", False)):
        return None

    composer = _Composer(app)
    try:
        # Guard ДО attach (то же решение, что и в прод-ветке).
        _register_guard_once(app, composer)

        platform = fakes.get("platform")
        if platform is None:
            build_platform = _load(
                "platform",
                "prodavan.application.plugins.wiring.platform",
                "build_plugin_platform",
            )
            test_overrides: dict[str, Any] = dict(fakes.get("platform_overrides") or {})
            for name in ("grant_store", "cosign", "admin_ports"):
                if fakes.get(name) is None:
                    raise PluginCompositionError(
                        "platform", f"install_plugin_platform_for_tests требует fakes[{name!r}]"
                    )
                test_overrides.setdefault(name, fakes[name])
            runtime_ports = fakes.get("runtime_ports")
            if runtime_ports is not None and hasattr(runtime_ports, "as_platform_overrides"):
                for key, value in runtime_ports.as_platform_overrides().items():
                    test_overrides.setdefault(key, value)
            for name in ("gateway_auth", "gateway_proxy", "admin", "revocation"):
                if fakes.get(name) is not None:
                    test_overrides.setdefault(name, fakes[name])
            platform = _wrap("platform", build_platform, settings, **test_overrides)
        if platform is None:
            raise PluginCompositionError(
                "platform", "build_plugin_platform вернул None при enabled=True"
            )

        mcp_discovery = fakes.get("mcp_discovery")
        if mcp_discovery is not None:
            attach_discovery = _load(
                "mcp_discovery",
                "prodavan.application.plugins.wiring.adapters",
                "attach_mcp_discovery",
            )
            _wrap(
                "mcp_discovery", attach_discovery, platform.registry, mcp_discovery
            )

        gateway_runtime = fakes.get("gateway_runtime")
        admin_ports = fakes.get("admin_ports")
        admin_service = fakes.get("admin_service")
        if admin_service is None and admin_ports is not None:
            from prodavan.application.plugins.admin.service import PluginAdminService

            admin_service = _wrap("admin_ports", PluginAdminService, admin_ports)

        attach = _load(
            "attach_platform",
            "prodavan.application.plugins.wiring.state",
            "attach_plugin_platform",
        )
        from prodavan.application.plugins.wiring.state import STATE_KEYS  # лениво

        _wrap("attach_platform", attach, app, platform)
        composer.mark(*STATE_KEYS)
        if mcp_discovery is not None:
            setattr(composer._state, MCP_DISCOVERY_STATE_KEY, mcp_discovery)
            composer.mark(MCP_DISCOVERY_STATE_KEY)

        if gateway_runtime is not None:
            install_runtime = _load(
                "gateway_install",
                "prodavan.api.v1.plugin_gateway",
                "install_plugin_gateway_runtime",
            )
            _wrap("gateway_install", install_runtime, app, gateway_runtime)
            composer.mark("plugin_gateway_runtime")

        if admin_ports is not None and admin_service is not None:
            attach_admin = _load(
                "admin_attach",
                "prodavan.application.plugins.platform_admin.state",
                "attach_admin_runtime",
            )
            _wrap("admin_attach", attach_admin, app, ports=admin_ports, service=admin_service)
            composer.mark("plugin_admin_ports", "plugin_admin_service")

        setattr(composer._state, UNWIRED_PORTS_STATE_KEY, ())
        composer.mark(UNWIRED_PORTS_STATE_KEY)
        setattr(composer._state, IN_MEMORY_PORTS_STATE_KEY, ())
        composer.mark(IN_MEMORY_PORTS_STATE_KEY)
        return platform
    except Exception:
        composer.rollback()
        raise
