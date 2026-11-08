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
    c) build_plugin_admin_ports(...)               -> PluginAdminPorts (все 10 полей)
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

import importlib
import logging
from typing import Any, Final, Mapping

logger = logging.getLogger(__name__)

#: Флаг в ``app.state``: guard уже зарегистрирован ровно один раз. Имя намеренно
#: не совпадает ни с одним из ``wiring.state.STATE_KEYS`` / admin / gateway.
PLUGIN_SURFACE_GUARD_STATE_KEY: Final[str] = "plugin_surface_guard_registered"

#: Диагностика: порты без прод-фабрики (задачи A10/14/15). НЕ секреты.
UNWIRED_PORTS_STATE_KEY: Final[str] = "plugin_unwired_ports"

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
)

__all__ = [
    "PLUGIN_SURFACE_GUARD_STATE_KEY",
    "UNWIRED_PORTS_STATE_KEY",
    "PluginCompositionError",
    "install_plugin_platform",
    "install_plugin_platform_for_tests",
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
        raise PluginCompositionError(step, f"{type(exc).__name__}: {exc}") from exc


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

    def rollback(self) -> None:
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


# ---------------------------------------------------------------------------
# Авто-композиция: реальные сервисы зон + честные заглушки без фабрики.
# ---------------------------------------------------------------------------


def _zone_services(runtime: Any, repos: Any, config: Any, ov: dict[str, Any], unwired: list[str]) -> dict[str, Any]:
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
    object_buckets = need("object_buckets")
    object_store = PluginObjectStore(
        buckets=object_buckets,
        sts=ov.get("object_sts"),
        presigner=ov.get("object_presigner"),
        audit=repos.audit,
    )
    tenant_db = TenantDatabaseService(
        admin=need("tenant_db_admin"),
        secrets=need("tenant_db_secrets"),
        audit=repos.audit,
        dsn_host=config.tenant_db_host,
        dsn_port=config.tenant_db_port,
        dsn_sslmode="require",
    )
    activator = PluginActivator(
        scale=runtime.scale,
        readiness=runtime.readiness,
        clock=ov.get("clock") or SystemClock(),
        mint=need("activation_mint"),
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
        "artifact_registry": artifact_registry,
        "object_store": object_store,
        "object_buckets": object_buckets,
        "tenant_db": tenant_db,
        "activator": activator,
    }


def _admin_ports(
    runtime: Any, repos: Any, config: Any, ov: dict[str, Any], unwired: list[str],
    services_out: dict[str, Any],
) -> Any:
    """Шаг (c): ``PluginAdminPorts`` через ``build_plugin_admin_ports``.

    Все 10 полей обязательны. preferably SQL-репозитории, а не in-memory:
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
    services = _wrap("admin_ports", _zone_services, runtime, repos, config, ov, unwired)
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
        from prodavan.application.plugins.adapters.stores import InMemoryRouteRegistry

        routes = _wrap("admin_ports", InMemoryRouteRegistry)
        unwired.append("routes (InMemoryRouteRegistry: кластеру нужна БД/Redis, A10)")
    instance_resolver = ov.get("instance_resolver")
    if instance_resolver is None:
        from prodavan.application.plugins.adapters.activation_adapter import (
            InMemoryInstanceResolver,
        )

        instance_resolver = _wrap("admin_ports", InMemoryInstanceResolver)
        unwired.append("instance_resolver (in-memory; кластеру нужна БД, A10)")
    deployment_admin = need("deployment_admin")

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
        "deployment_admin": deployment_admin,
        "grant_store": ov.get("admin_grant_store") or repos.grant_store,
        "routes": routes,
        "audit": ov.get("admin_audit") or repos.audit,
    }
    return _wrap("admin_ports", build, **kwargs)


def _gateway_runtime(
    runtime: Any, repos: Any, redis: Any, ov: dict[str, Any], unwired: list[str],
    services: Mapping[str, Any] | None = None,
) -> Any:
    """Шаг (d): ``PluginGatewayRuntime`` через ``build_gateway_runtime``.

    Автоматически (реализации ЕСТЬ): ``hmac_verifier`` (``Sha256HmacAdapter``),
    ``rate_limit`` (``RedisRateLimitAdapter(redis)``), ``audit`` (``AuditAdapter``
    поверх append-only SQL-sink через тонкий ``write``-адаптер), ``activation``
    (``ActivationAdapter`` -> ``PluginActivator``), ``backend_http``/``static_http``
    из ``RuntimePorts``. У ``SessionPort``/``GrantPort``/``SecretPort`` нет
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
        from prodavan.application.plugins.adapters.activation_adapter import (
            InMemoryInstanceResolver,
        )

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
            or _wrap("gateway_runtime", InMemoryInstanceResolver),
            readiness=runtime.readiness,
        )
    kwargs: dict[str, Any] = {
        "sessions": need("gateway_sessions"),
        "grants": need("gateway_grants"),
        "secrets": need("gateway_secrets"),
        "rate_limit": ov.get("gateway_rate_limit_port") or RedisRateLimitAdapter(redis),
        "audit": audit,
        "hmac_verifier": ov.get("gateway_hmac_port") or Sha256HmacAdapter(),
        "activation": activation,
        "backend_http": ov.get("gateway_backend_http") or runtime.backend_http,
        "static_http": ov.get("gateway_static_http") or runtime.backend_http,
        "limits": ov.get("gateway_limits") or DEFAULT_LIMITS,
    }
    return _wrap("gateway_runtime", build, **kwargs)


def _gateway_activator(runtime: Any, ov: dict[str, Any], need: Any) -> Any:
    from prodavan.application.plugins.activation.activator import PluginActivator, SystemClock
    from prodavan.application.plugins.activation.state import IdlePolicy

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


def _platform_overrides(
    repos: Any, runtime: Any, admin_ports: Any, gateway_runtime: Any,
    admin_service: Any, ov: dict[str, Any], services: Mapping[str, Any] | None = None,
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
    # ``RuntimePorts.as_platform_overrides()``: cosign / activation_scale /
    # activation_readiness / activation_state_store / activation_proxy /
    # gateway_backend_http. У тестовых даблов метод может отсутствовать.
    if hasattr(runtime, "as_platform_overrides"):
        for key, value in _wrap("runtime_ports", runtime.as_platform_overrides).items():
            out.setdefault(key, value)
    # Явные overrides родителя имеют приоритет, но НЕ могут обнулить порт.
    for key, value in dict(ov.get("platform_overrides") or {}).items():
        if value is None:
            raise PluginCompositionError(
                "platform", f"platform_overrides[{key!r}] is None; fail-closed"
            )
        out[key] = value
    return out


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
    # ОДНИ и те же реальные сервисы зон использует и admin (шаг c), и gateway
    # (шаг d), и platform (шаг e): иначе capability-запросы, отзыв и wake
    # смотрели бы в разные объекты зон.
    zone_services: dict[str, Any] = {}
    try:
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

        # --- b) runtime-порты: cosign/k8s/state/proxy ----------------------
        runtime = ov.get("runtime_ports")
        if runtime is None:
            build_runtime = _load(
                "runtime_ports",
                "prodavan.infrastructure.plugins.runtime.build",
                "build_runtime_ports",
            )
            runtime = _wrap(
                "runtime_ports", build_runtime, settings,
                **dict(ov.get("runtime_kwargs") or {}),
            )
        if runtime is None:
            raise PluginCompositionError("runtime_ports", "build_runtime_ports вернул None")

        # --- c) PluginAdminPorts (10 полей) + PluginAdminService ------------
        admin_ports = ov.get("admin_ports") or _wrap(
            "admin_ports", _admin_ports, runtime, repos, config, ov, unwired, zone_services
        )
        admin_service = ov.get("admin_service")
        if admin_service is None:
            from prodavan.application.plugins.admin.service import PluginAdminService

            admin_service = _wrap("admin_ports", PluginAdminService, admin_ports)
        if admin_ports is None or admin_service is None:
            raise PluginCompositionError("admin_ports", "admin runtime не собран")

        # --- d) PluginGatewayRuntime ----------------------------------------
        gateway_runtime = ov.get("gateway_runtime") or _wrap(
            "gateway_runtime", _gateway_runtime, runtime, repos, redis, ov, unwired,
            zone_services,
        )
        if gateway_runtime is None:
            raise PluginCompositionError("gateway_runtime", "build_gateway_runtime вернул None")

        # --- e) build_plugin_platform ---------------------------------------
        platform_overrides = _wrap(
            "platform", _platform_overrides,
            repos, runtime, admin_ports, gateway_runtime, admin_service, ov,
        )
        platform = ov.get("platform")
        if platform is None:
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

        # --- f) attach_plugin_platform -> STATE_KEYS wiring (19) -------------
        attach = _load(
            "attach_platform",
            "prodavan.application.plugins.wiring.state",
            "attach_plugin_platform",
        )
        from prodavan.application.plugins.wiring.state import STATE_KEYS  # лениво

        _wrap("attach_platform", attach, app, platform)
        composer.mark(*STATE_KEYS)

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
        setattr(composer._state, UNWIRED_PORTS_STATE_KEY, tuple(sorted(set(unwired))))
        composer.mark(UNWIRED_PORTS_STATE_KEY)

        # Финальная сверка: ни одного «почти привязанного» ключа.
        for key in _OWN_STATE_KEYS:
            if not hasattr(composer._state, key):
                raise PluginCompositionError(
                    "verify", f"app.state.{key} отсутствует после композиции"
                )
        if unwired:
            logger.warning(
                "plugin platform installed with %d unwired ports (F11 debt): %s",
                len(set(unwired)), ", ".join(sorted(set(unwired))),
            )
        else:
            logger.info("plugin platform installed: all app.state keys bound, guard once")
        return platform
    except Exception:
        composer.rollback()
        raise


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
        return platform
    except Exception:
        composer.rollback()
        raise
