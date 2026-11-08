"""Единая точка сборки plugin-платформы (F11, composition root).

Родитель подключает платформу в ``main.py`` так (САМ, этот модуль ``main.py``
НЕ редактирует)::

    from prodavan.application.plugins.wiring import build_plugin_platform, attach_plugin_platform
    from prodavan.config.settings import settings

    platform = build_plugin_platform(settings, grant_store=..., cosign=..., ...)
    if platform is not None:
        attach_plugin_platform(app, platform)

``enabled=False`` -> ``None``: ни одного сервиса не создаётся, ``app.state``
не получает platform, а plugin-роутеры (задачи 04/11/12) отдают 503 через
``get_plugin_platform`` / ``transport.deps.resolve_identity_config`` /
``transport.bridge.get_capability_service``.

Интеграционная цепочка ``verify -> check -> assert_plugin_surface``
------------------------------------------------------------------
Это КЛЮЧЕВОЙ контракт платформы (v4 §4.1/§4.2, критерии №12 и №17). Каждый
запрос на ``/capabilities/v1/*`` проходит ровно три шага, в этом порядке:

1. **verify** — ``identity.mint.verify_plugin_token(token, keyring=platform.keyring)``.
   Подпись по ``kid`` из ``KeyRing`` (активный + ротационные ключи), затем
   ``iss/aud/typ``, обязательные claim, ``exp``/``iat``, ``jti``, формат
   ``artifact_digest``. Никаких dev-фолбэков: пустой/чужой ключ = отказ.
   Реализует ``transport.deps.get_plugin_claims`` / ``identity``.
2. **check** — ``platform.revocation.check(claims)`` (или
   ``GrantRevocation(platform.revocation).check``). Проверка ``grant_id`` на
   КАЖДОМ запросе по ``CapabilityGrantRow.status`` из БД (A10). Отзыв grant'а
   мгновенно отклоняет ещё не истёкший короткий JWT. Это НЕ
   ``bump_pod_bridge_generation`` (тот привязан к ``pod_id`` + Redis агентов).
   ``PluginCapabilityService`` дополнительно вызывает тот же порт через
   ``is_active(grant_id, *, now)`` перед квотой/адаптером.
3. **assert_plugin_surface** — ``identity.allowlist.assert_plugin_surface(
   claims, request.url.path)``. Плагину разрешено ТОЛЬКО
   ``^/capabilities/v1/<capability>(/...)``. Agent-surface (``/agent/*``,
   ``/internal/pods/*``, ``/projects/*/modules/*``, ``.../infra``,
   ``documents``, ``web-search``, ``web-fetch``), а также ``/webhook/*`` и
   ``/pluginpage/*`` закрыты. Без этого шага повторяется ошибка v2/v3:
   смешение trust даёт плагину ``/internal/pods/{id}/workspace-archive``.

После цепочки ``PluginCapabilityService.invoke`` выполняет ядро
``CapabilityGatewayService`` (identity -> scope -> grant -> quota -> adapter
-> append-only audit), а адаптеры разрешаются через ``platform.registry``.
Незаполненный адаптер = ``UnavailableAdapter`` -> ``AdapterMissingError`` ->
system deny, а не «200 с None» (см. ``adapters.py``).

Все зависимости — через инъекцию портов
---------------------------------------
``build_plugin_platform(settings, **overrides)`` не создаёт ни БД-сессий, ни
HTTP-клиентов, ни k8s-клиента, ни cosign-CLI: всё внешнее приходит в
``overrides``. Тесты подменяют fakes и проверяют композицию без IO. Если
обязательный порт не передан, собирается fail-closed реализация
(``DurableGrantRevocation`` требует ``grant_store``; ``ArtifactVerifier``
требует ``cosign``) — «частично собранной» платформы с тихим allow не бывает.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Final, Mapping

from prodavan.application.plugins.activation.activator import PluginActivator, SystemClock
from prodavan.application.plugins.activation.state import IdlePolicy
from prodavan.application.plugins.activation.state_store import InMemoryStateStore
from prodavan.application.plugins.admin.ports import PluginAdminPorts
from prodavan.application.plugins.admin.service import PluginAdminService
from prodavan.application.plugins.artifacts.registry import (
    InMemoryRegistryStore,
    PluginArtifactRegistry,
)
from prodavan.application.plugins.artifacts.verification import (
    PLATFORM_VERSION,
    ArtifactVerifier,
    CompatibilityGate,
    CosignPort,
)
from prodavan.application.plugins.capabilities.audit import AuditPort, InMemoryAudit
from prodavan.application.plugins.capabilities.ports import AdapterRegistry
from prodavan.application.plugins.capabilities.quotas import (
    InMemoryQuota,
    PluginQuota,
    QuotaPort,
)
from prodavan.application.plugins.capabilities.service import PluginCapabilityService
from prodavan.application.plugins.egress.generator import EgressConfigGenerator
from prodavan.application.plugins.egress.service import EgressService
from prodavan.application.plugins.events.dispatch import PluginEventDispatcher
from prodavan.application.plugins.gateway.auth import DEFAULT_LIMITS, GatewayAuth
from prodavan.application.plugins.gateway.proxy import GatewayProxy
from prodavan.application.plugins.identity.keys import KeyRing
from prodavan.application.plugins.objects.service import PluginObjectStore
from prodavan.application.plugins.tenantdb.service import TenantDatabaseService
from prodavan.application.plugins.wiring.adapters import (
    STUB_ADAPTER_NAMES,
    adapter_report,
    build_adapter_registry,
)
from prodavan.application.plugins.wiring.config import (
    PluginPlatformConfig,
    PluginPlatformConfigError,
)
from prodavan.application.plugins.wiring.keyring import build_keyring, jwks_for
from prodavan.application.plugins.wiring.revocation import DurableGrantRevocation

# Имена override'ов, которые являются именно ПОРТАМИ/зависимостями, а не
# готовыми сервисами платформы. Используется для валидации ``**overrides``:
# опечатка в имени порта не должна тихо теряться.
PORT_OVERRIDES: Final[frozenset[str]] = frozenset(
    {
        "grant_store",
        "cosign",
        "manifest_validator",
        "quota",
        "audit",
        "clock",
        # Порты зон (все через инъекцию; без них собирается fail-closed объект
        # соответствующей зоны, который откажет при первом же вызове).
        "tenant_db_admin",
        "tenant_db_secrets",
        "tenant_db_audit",
        "object_buckets",
        "object_sts",
        "object_presigner",
        "object_audit",
        "artifact_registry_store",
        "egress_grant_store",
        "egress_audit",
        "egress_hop_inspector",
        "event_producer",
        "event_consumer",
        "event_topic_admin",
        "event_dlq",
        "event_subscriptions",
        "event_published_events",
        "event_idempotency",
        "event_audit",
        "event_handler",
        "event_wake_hook",
        "activation_scale",
        "activation_readiness",
        "activation_clock",
        "activation_mint",
        "activation_audit",
        "activation_state_store",
        "activation_proxy",
        "gateway_sessions",
        "gateway_grants",
        "gateway_secrets",
        "gateway_rate_limit",
        "gateway_audit",
        "gateway_activation",
        "gateway_backend_http",
        "gateway_static_http",
        "admin_ports",
        "egress_policy",
    }
)

# Имена готовых СЕРВИСОВ, которые можно подменить целиком (для тестов).
SERVICE_OVERRIDES: Final[frozenset[str]] = frozenset(
    {
        "config",
        "keyring",
        "revocation",
        "capability_service",
        "artifact_verifier",
        "artifact_registry",
        "registry",
        "tenant_db",
        "object_store",
        "egress",
        "activator",
        "gateway_auth",
        "gateway_proxy",
        "admin",
        "event_dispatcher",
    }
)

# Реализации capability-адаптеров: ``adapter_<capability>``.
ADAPTER_OVERRIDES: Final[frozenset[str]] = frozenset(
    f"adapter_{name}" for name in STUB_ADAPTER_NAMES
)

OVERRIDE_NAMES: Final[frozenset[str]] = (
    PORT_OVERRIDES | SERVICE_OVERRIDES | ADAPTER_OVERRIDES
)


@dataclass(frozen=True, slots=True)
class PluginPlatform:
    """Собранная платформа: все сервисы одной неизменяемой связкой.

    Поля ровно по ТЗ: ``config``, ``keyring``, ``revocation``,
    ``capability_service``, ``artifact_verifier``, ``registry``, ``tenant_db``,
    ``object_store``, ``egress``, ``activator``, ``gateway``, ``admin``,
    ``event_dispatcher``.

    ``gateway`` — пара ``GatewayAuth`` + ``GatewayProxy`` (задача 11 разделяет
    «решить» и «исполнить», §4.5); обе части доступны и по отдельности через
    ``gateway_auth`` / ``gateway_proxy``. ``registry`` — реестр адаптеров
    capability (НЕ путать с ``PluginArtifactRegistry`` — последний живёт в
    поле ``artifact_registry``).

    Тип ``revocation`` намеренно структурный (``object``): штатно это
    ``DurableGrantRevocation``, но тесты/F11 вправе подменить реализацию
    порта; сборка проверяет контракт по методам, а не по ``isinstance``.
    """

    config: PluginPlatformConfig
    keyring: KeyRing
    revocation: object  # RevocationPort + GrantRevocationPort + check(claims)
    capability_service: PluginCapabilityService
    artifact_verifier: ArtifactVerifier
    artifact_registry: PluginArtifactRegistry
    registry: AdapterRegistry
    tenant_db: TenantDatabaseService
    object_store: PluginObjectStore
    egress: EgressService
    egress_generator: EgressConfigGenerator
    activator: PluginActivator
    gateway: tuple[GatewayAuth, GatewayProxy]
    admin: PluginAdminService
    event_dispatcher: PluginEventDispatcher

    @property
    def gateway_auth(self) -> GatewayAuth:
        """Решение об авторизации (задача 11, первая половина gateway)."""
        return self.gateway[0]

    @property
    def gateway_proxy(self) -> GatewayProxy:
        """Исполнение уже авторизованного запроса (вторая половина)."""
        return self.gateway[1]

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def jwks(self) -> dict[str, Any]:
        """Публичные ключи платформы (без симметричных секретов)."""
        return jwks_for(self.keyring)

    def report(self) -> Mapping[str, Any]:
        """Безопасная диагностическая проекция сборки (БЕЗ секретов/payload).

        Включает ЯВНЫЙ список адаптеров-заглушек: «молчаливого успеха» быть
        не должно, родитель видит, что требует F11-доработки.
        """
        return {
            "config": dict(self.config.as_report()),
            "keyring": {
                "active_kid": self.keyring.active_kid,
                "jwks": self.jwks,
            },
            "adapters": dict(adapter_report(self.registry)),
            "known_stub_adapters": list(STUB_ADAPTER_NAMES),
            "capability_service": type(self.capability_service).__name__,
            "artifact_verifier": type(self.artifact_verifier).__name__,
            "revocation": type(self.revocation).__name__,
            "tenant_db": type(self.tenant_db).__name__,
            "object_store": type(self.object_store).__name__,
            "egress": type(self.egress).__name__,
            "activator": type(self.activator).__name__,
            "gateway": [type(self.gateway_auth).__name__, type(self.gateway_proxy).__name__],
            "admin": type(self.admin).__name__,
            "event_dispatcher": type(self.event_dispatcher).__name__,
        }


def _validate_overrides(overrides: Mapping[str, Any]) -> None:
    unknown = sorted(set(overrides) - OVERRIDE_NAMES)
    if unknown:
        raise PluginPlatformConfigError(
            "unknown build_plugin_platform override(s): " + ", ".join(unknown)
        )


def _service(name: str, overrides: Mapping[str, Any], factory: Any) -> Any:
    """Взять готовый сервис из overrides или собрать через ``factory()``."""
    if name in overrides:
        value = overrides[name]
        if value is None:
            raise PluginPlatformConfigError(
                f"override {name!r} is None; fail-closed composition forbids "
                "explicitly disabling a required platform service"
            )
        return value
    return factory()


def _adapter_overrides(overrides: Mapping[str, Any]) -> dict[str, object]:
    """Реальные адаптеры capability передаются как ``adapter_<name>``.

    Отдельный префикс исключает коллизию с портами зон (например,
    ``object_buckets`` — порт MinIO, а ``adapter_objects`` — реализация
    capability ``objects`` для ``AdapterRegistry``).
    """
    prefix = "adapter_"
    return {
        key[len(prefix):]: value
        for key, value in overrides.items()
        if key.startswith(prefix)
    }


def build_plugin_platform(settings: Any, **overrides: Any) -> PluginPlatform | None:
    """Собрать платформу или вернуть ``None`` (платформа выключена).

    Шаги:
      1. ``PluginPlatformConfig.from_settings(settings)`` + валидация guard'ов;
      2. ``enabled=False`` -> ``None`` (НИ ОДНОГО сервиса не создаётся);
      3. KeyRing (активный секрет + ротация), DurableGrantRevocation (БД A10),
         AdapterRegistry (9 имён, ``db.direct`` исключён);
      4. все зоны (capabilities/artifacts/tenantdb/objects/egress/activation/
         events/gateway/admin) через инъекцию портов;
      5. неизменяемый ``PluginPlatform``.

    Любая ошибка композиции = исключение (fail-closed): родитель НЕ должен
    подключать «почти собранную» платформу. Тесты подменяют fakes через
    ``overrides`` и не делают IO.
    """
    _validate_overrides(overrides)

    config = _service(
        "config",
        overrides,
        lambda: PluginPlatformConfig.from_settings(settings),
    )
    if not isinstance(config, PluginPlatformConfig):
        raise PluginPlatformConfigError("config override must be a PluginPlatformConfig")
    config.validate()
    if not config.enabled:
        # Платформа выключена: роутеры отдадут 503. Ни keyring, ни БД, ни
        # адаптеры, ни сервисы не создаются вообще.
        return None

    # --- 1) identity -------------------------------------------------------
    keyring = _service("keyring", overrides, lambda: build_keyring(config))
    if not isinstance(keyring, KeyRing):
        raise PluginPlatformConfigError("keyring override must be an identity KeyRing")

    # --- 2) отзыв grant'ов (источник истины — БД A10) ---------------------
    def _build_revocation() -> DurableGrantRevocation:
        store = overrides.get("grant_store")
        if store is None:
            # Fail-closed: in-memory подмена источника истины запрещена.
            raise PluginPlatformConfigError(
                "build_plugin_platform requires grant_store= (GrantStorePort over "
                "CapabilityGrantRow.status, задача A10); no in-memory fallback"
            )
        return DurableGrantRevocation(store, clock=overrides.get("clock"))

    revocation = _service("revocation", overrides, _build_revocation)
    # Допускаем пользовательскую реализацию порта (тесты/F11-адаптер), но
    # требуем тот же контракт: is_active/revoke/is_revoked/check, без in-memory
    # «источника истины» задачи 02.
    for method in ("is_active", "revoke", "is_revoked", "check"):
        if not callable(getattr(revocation, method, None)):
            raise PluginPlatformConfigError(
                f"revocation override is missing {method!r}; durable port contract violated"
            )

    # --- 3) capability-ядро + реестр адаптеров ----------------------------
    registry = _service(
        "registry",
        overrides,
        lambda: build_adapter_registry(config, **_adapter_overrides(overrides)),
    )
    if not isinstance(registry, AdapterRegistry):
        raise PluginPlatformConfigError("registry override must be an AdapterRegistry")

    audit: AuditPort = overrides.get("audit") or InMemoryAudit()
    quota: QuotaPort = overrides.get("quota") or InMemoryQuota(PluginQuota())
    egress_policy = overrides.get("egress_policy")

    capability_service = _service(
        "capability_service",
        overrides,
        lambda: PluginCapabilityService(
            registry=registry,
            quota=quota,
            audit=audit,
            revocation=revocation,  # тот же объект implements GrantRevocationPort
            egress_policy=egress_policy,
        ),
    )

    # --- 4) артефакты (cosign обязателен, иначе карантин) ------------------
    def _build_verifier() -> ArtifactVerifier:
        cosign = overrides.get("cosign")
        if cosign is None or not isinstance(cosign, CosignPort):
            raise PluginPlatformConfigError(
                "build_plugin_platform requires cosign= (CosignPort); "
                "ArtifactVerifier без проверки подписи публиковать не может"
            )
        if not config.cosign_identity.strip():
            # ``--certificate-identity`` обязателен для keyless cosign. Пустая
            # строка/«unset» давала бы тихий карантин ВСЕХ артефактов вместо
            # честной ошибки композиции (fail-closed, но не диагностируемой).
            raise PluginPlatformConfigError(
                "PLUGIN_COSIGN_IDENTITY is required to build ArtifactVerifier "
                "(keyless cosign --certificate-identity); no placeholder allowed"
            )
        return ArtifactVerifier(
            cosign,
            validator=overrides.get("manifest_validator"),
            identity=config.cosign_identity,
            issuer=config.cosign_issuer,
            # Версия платформы — константа задачи 07; ``min_platform_version``
            # из настроек — это нижняя граница совместимости manifest'а, а НЕ
            # версия самой платформы (не подменяем PLATFORM_VERSION).
            compatibility=CompatibilityGate(platform_version=PLATFORM_VERSION),
            clock=overrides.get("clock"),
        )

    artifact_verifier = _service("artifact_verifier", overrides, _build_verifier)
    artifact_registry = _service(
        "artifact_registry",
        overrides,
        lambda: PluginArtifactRegistry(
            store=overrides.get("artifact_registry_store") or InMemoryRegistryStore(),
            verifier=artifact_verifier,
            clock=overrides.get("clock"),
        ),
    )

    # --- 5) зоны: tenantdb / objects / egress ------------------------------
    tenant_db = _service(
        "tenant_db",
        overrides,
        lambda: TenantDatabaseService(
            admin=overrides.get("tenant_db_admin"),
            secrets=overrides.get("tenant_db_secrets"),
            audit=overrides.get("tenant_db_audit"),
            dsn_host=config.tenant_db_host,
            dsn_port=config.tenant_db_port,
            # fail-closed: TLS require, даже если администратор забыл (задача 05).
            dsn_sslmode="require",
        ),
    )
    object_store = _service(
        "object_store",
        overrides,
        lambda: PluginObjectStore(
            buckets=overrides.get("object_buckets") or _MissingObjectPorts("buckets"),
            sts=overrides.get("object_sts"),
            presigner=overrides.get("object_presigner"),
            audit=overrides.get("object_audit") or _MissingObjectPorts("audit"),
        ),
    )
    egress = _service(
        "egress",
        overrides,
        lambda: EgressService(
            store=overrides.get("egress_grant_store") or _MissingEgressStore(),
            audit=overrides.get("egress_audit"),
            hop_inspector=overrides.get("egress_hop_inspector"),
        ),
    )
    egress_generator = EgressConfigGenerator()

    # --- 6) activation / events -------------------------------------------
    activator = _service(
        "activator",
        overrides,
        lambda: PluginActivator(
            scale=overrides.get("activation_scale") or _MissingActivationPort("scale"),
            readiness=overrides.get("activation_readiness") or _MissingActivationPort("readiness"),
            clock=overrides.get("activation_clock") or SystemClock(),
            mint=overrides.get("activation_mint") or _MissingActivationPort("mint"),
            audit=overrides.get("activation_audit") or _MissingActivationPort("audit"),
            policy=IdlePolicy(
                idle_ttl_seconds=float(config.idle_ttl_seconds),
                ready_timeout_seconds=float(config.ready_timeout_seconds),
                max_concurrent_wakes=int(config.max_concurrent_wakes),
            ),
            state_store=overrides.get("activation_state_store") or InMemoryStateStore(),
            proxy=overrides.get("activation_proxy"),
        ),
    )
    event_dispatcher = _service(
        "event_dispatcher",
        overrides,
        lambda: PluginEventDispatcher(
            producer=overrides.get("event_producer"),
            consumer=overrides.get("event_consumer"),
            topic_admin=overrides.get("event_topic_admin"),
            dlq=overrides.get("event_dlq"),
            subscriptions=overrides.get("event_subscriptions"),
            published_events=overrides.get("event_published_events"),
            idempotency=overrides.get("event_idempotency"),
            audit=overrides.get("event_audit"),
            handler=overrides.get("event_handler"),
            wake_hook=overrides.get("event_wake_hook"),
        ),
    )

    # --- 7) gateway (решить / исполнить) + admin ---------------------------
    gateway_auth = _service(
        "gateway_auth",
        overrides,
        lambda: GatewayAuth(
            sessions=overrides.get("gateway_sessions") or _MissingGatewayPort("sessions"),
            grants=overrides.get("gateway_grants") or _MissingGatewayPort("grants"),
            secrets=overrides.get("gateway_secrets") or _MissingGatewayPort("secrets"),
            rate_limit=overrides.get("gateway_rate_limit") or _MissingGatewayPort("rate_limit"),
            audit=overrides.get("gateway_audit") or _MissingGatewayPort("audit"),
            limits=DEFAULT_LIMITS,
        ),
    )
    gateway_proxy = _service(
        "gateway_proxy",
        overrides,
        lambda: GatewayProxy(
            activation=overrides.get("gateway_activation") or _MissingGatewayPort("activation"),
            backend_http=overrides.get("gateway_backend_http") or _MissingGatewayPort("backend_http"),
            audit=overrides.get("gateway_audit") or _MissingGatewayPort("audit"),
            static_http=overrides.get("gateway_static_http"),
        ),
    )

    def _build_admin() -> PluginAdminService:
        ports = overrides.get("admin_ports")
        if ports is None:
            # Собрать «полуподключённый» PluginAdminPorts нельзя (все поля
            # обязательны, задача 12). Явно отказываем, а не подставляем fakes.
            raise PluginPlatformConfigError(
                "build_plugin_platform requires admin_ports= (PluginAdminPorts, задача 12); "
                " Admin API без портов не должен существовать"
            )
        if not isinstance(ports, PluginAdminPorts):
            raise PluginPlatformConfigError("admin_ports must be a PluginAdminPorts instance")
        return PluginAdminService(ports, clock=overrides.get("clock"))

    admin = _service("admin", overrides, _build_admin)

    platform = PluginPlatform(
        config=config,
        keyring=keyring,
        revocation=revocation,
        capability_service=capability_service,
        artifact_verifier=artifact_verifier,
        artifact_registry=artifact_registry,
        registry=registry,
        tenant_db=tenant_db,
        object_store=object_store,
        egress=egress,
        egress_generator=egress_generator,
        activator=activator,
        gateway=(gateway_auth, gateway_proxy),
        admin=admin,
        event_dispatcher=event_dispatcher,
    )
    # Финальная проверка: ни одно поле не осталось None (fail-closed).
    for item in fields(platform):
        if getattr(platform, item.name) is None:
            raise PluginPlatformConfigError(f"platform field {item.name!r} is None")
    return platform


# ---------------------------------------------------------------------------
# Fail-closed «порты-пустышки» для необязательных на конструкторе зон.
#
# Зоны 05/06/09/11 объявляют часть портов опциональными НА КОНСТРУКТОРЕ, но
# НЕ на уровне вызова (см. их docstring'ы). Вместо того чтобы оставлять None и
# надеяться, что никто не вызовет, подкладываем объект, который при первом же
# обращении бросает понятную ошибку композиции. Это НЕ «молчаливый успех» и
# НЕ подмена реальной интеграции: родитель всё равно обязан передать порты.
# ---------------------------------------------------------------------------


class _MissingPort:
    """Любой вызов -> ``PluginPlatformConfigError`` (fail-closed)."""

    __slots__ = ("_zone", "_name")

    def __init__(self, zone: str, name: str) -> None:
        self._zone = zone
        self._name = name

    def __getattr__(self, method: str) -> Any:
        def refuse(*args: Any, **kwargs: Any) -> Any:
            raise PluginPlatformConfigError(
                f"plugin {self._zone} port {self._name!r}.{method}() is not wired "
                "(F11: pass a real adapter via build_plugin_platform overrides)"
            )

        refuse.__name__ = method
        return refuse


def _MissingObjectPorts(name: str) -> _MissingPort:
    return _MissingPort("objects", name)


def _MissingActivationPort(name: str) -> _MissingPort:
    return _MissingPort("activation", name)


def _MissingGatewayPort(name: str) -> _MissingPort:
    return _MissingPort("gateway", name)


def _MissingEgressStore() -> _MissingPort:
    return _MissingPort("egress", "grant_store")


__all__ = [
    "ADAPTER_OVERRIDES",
    "OVERRIDE_NAMES",
    "PORT_OVERRIDES",
    "SERVICE_OVERRIDES",
    "PluginPlatform",
    "build_plugin_platform",
]
