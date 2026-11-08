"""Сборка ``PluginAdminPorts`` из РЕАЛЬНЫХ реализаций (F11, родитель).

Главное правило (по заданию): **отсутствие ЛЮБОЙ из 10 реализаций = явная
ошибка, а не заглушка «успех»**. ``build_plugin_admin_ports`` не имеет ни
одного значения по умолчанию для портов и не подставляет «no-op» адаптеры:
недособранная связка просто не создаётся, а значит ``PluginAdminService``
не запускается и Admin API отвечает 503 (fail-closed по конструкции,
см. ``api/v1/plugin_admin.py::_ports``).

Что именно собирается
---------------------
============================  ==================================================
Поле ``PluginAdminPorts``      Источник
============================  ==================================================
``tenant_db``                 ``TenantDbAdapter(TenantDatabaseService)``
``object_store``              ``ObjectStoreAdapter(PluginObjectStore, BucketAdminPort)``
``artifact_registry``         ``ArtifactRegistryAdapter(PluginArtifactRegistry, store)``
``grant_store``               ``InMemoryGrantStore`` (в кластере — БД, A10)
``revocation``                ``IdentityRevocationAdapter(GrantRevocation/InMemoryRevocation)``
``mint``                      ``IdentityMintAdapter(secret=...)``
``activation``                ``AdminActivationAdapter(PluginActivator, resolver, deployments)``
``routes``                    ``InMemoryRouteRegistry`` (в кластере — БД/Redis, A10)
``audit``                     ``InMemoryAdminAudit`` (в кластере — append-only sink)
``platform_admin``            реальная auth родителя ИЛИ ``StaticPlatformAdmin`` ТОЛЬКО для тестов
============================  ==================================================

Функция намеренно принимает уже СОБРАННЫЕ зависимости соседних зон, а не
пытается создать их сама: адаптеры не читают ``settings``, не открывают
соединений к PostgreSQL/MinIO/k8s и не знают про секреты платформы. Родитель
(F11) делает это при старте приложения и кладёт результат в
``app.state.plugin_admin_ports`` / ``app.state.plugin_admin_service``.

ВАЖНО: ``platform`` — это не «флаг успеха», а ОБЯЗАТЕЛЬНЫЙ ``PlatformAdminPort``.
Передача ``None`` (или объекта без ``require_platform_admin``) = ``ValueError``.
Для prod/static-тестов можно передать ``StaticPlatformAdmin``, но в кластере
родитель обязан подключить настоящую auth (OIDC/JWKS + роль platform.admin).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from prodavan.application.plugins.admin.ports import (
    AuditPort,
    GrantStorePort,
    PlatformAdminPort,
    PluginAdminPorts,
    RouteRegistryPort,
)
from prodavan.application.plugins.admin.service import PluginAdminService

from .activation_adapter import (
    AdminActivationAdapter,
    DeploymentAdminPort,
    InstanceIdResolver,
)
from .artifacts_adapter import ArtifactRegistryAdapter
from .errors import require_wired
from .identity_adapter import IdentityMintAdapter, IdentityRevocationAdapter
from .objects_adapter import ObjectStoreAdapter
from .stores import (
    InMemoryAdminAudit,
    InMemoryGrantStore,
    InMemoryRouteRegistry,
)
from .tenantdb_adapter import TenantDbAdapter


@dataclass(frozen=True, slots=True)
class AdapterDependencies:
    """Явная связка реальных реализаций соседних зон.

    Ни одно поле не имеет значения по умолчанию: «частично подключённая»
    конфигурация невозможна. Типы намеренно широкие (``Any``), чтобы не
    тащить в этот модуль чужие датаклассы и не создавать циклов; проверка
    типов выполняется конструкторами адаптеров.
    """

    tenant_db_service: Any
    object_store_service: Any
    bucket_admin: Any
    artifact_registry: Any
    artifact_store: Any | None
    revocation_source: Any
    mint_secret: str | bytes
    activator: Any
    instance_resolver: Any
    deployment_admin: Any


def build_plugin_admin_ports(
    *,
    platform: PlatformAdminPort,
    tenant_db_service: Any,
    object_store_service: Any,
    bucket_admin: Any,
    artifact_registry: Any,
    revocation_source: Any,
    mint_secret: str | bytes,
    activator: Any,
    instance_resolver: Any,
    deployment_admin: Any,
    artifact_store: Any | None = None,
    grant_store: GrantStorePort | None = None,
    routes: RouteRegistryPort | None = None,
    audit: AuditPort | None = None,
    resolver_registrar: Any | None = None,
) -> PluginAdminPorts:
    """Собрать ВСЕ 10 портов ``PluginAdminService``.

    ``platform`` и 10 зависимостей обязательны. ``grant_store``/``routes``/
    ``audit`` по умолчанию — in-memory реализации ТОЛЬКО для dev/тестов;
    в кластере родитель ДОЛЖЕН передать реализации поверх БД/Redis
    (задача A10). Явная пометка здесь и в ``stores.py`` — часть контракта.

    Отсутствие ЛЮБОЙ обязательной реализации → ``ValueError`` с именем порта.
    Никаких «заглушек успеха»: если адаптер не собран, сервис не существует.
    """

    # --- 1. Явная проверка всех обязательных зависимостей ДО сборки ----------
    required: dict[str, Any] = {
        "platform": platform,
        "tenant_db_service": tenant_db_service,
        "object_store_service": object_store_service,
        "bucket_admin": bucket_admin,
        "artifact_registry": artifact_registry,
        "revocation_source": revocation_source,
        "mint_secret": mint_secret,
        "activator": activator,
        "instance_resolver": instance_resolver,
        "deployment_admin": deployment_admin,
    }
    for name, value in required.items():
        require_wired(name, value)

    if not isinstance(mint_secret, (str, bytes)) or not mint_secret:
        raise ValueError("build_plugin_admin_ports: mint_secret должен быть непустым (без dev-фолбэков)")
    if not isinstance(instance_resolver, InstanceIdResolver):
        raise TypeError("build_plugin_admin_ports: instance_resolver обязан реализовывать InstanceIdResolver")
    if not isinstance(deployment_admin, DeploymentAdminPort):
        raise TypeError("build_plugin_admin_ports: deployment_admin обязан реализовывать DeploymentAdminPort")
    if not callable(getattr(platform, "require_platform_admin", None)):
        # ``admin.PlatformAdminPort`` НЕ помечен @runtime_checkable, поэтому
        # ``isinstance(platform, PlatformAdminPort)`` всегда бросил бы TypeError
        # (даже для корректной реализации). Проверяем структуру через атрибут.
        raise TypeError(
            "build_plugin_admin_ports: platform обязан реализовывать PlatformAdminPort"
        )

    # --- 2. Адаптеры к реальным сервисам соседних зон ------------------------
    tenant_db = TenantDbAdapter(tenant_db_service)
    objects = ObjectStoreAdapter(
        store=object_store_service,
        buckets=bucket_admin,
    )
    artifacts = ArtifactRegistryAdapter(
        registry=artifact_registry,
        store=artifact_store,
    )
    revocation = IdentityRevocationAdapter(revocation_source)
    mint = IdentityMintAdapter(secret=mint_secret)
    activation = AdminActivationAdapter(
        activator=activator,
        resolver=instance_resolver,
        deployments=deployment_admin,
        resolver_registrar=resolver_registrar,
    )

    # --- 3. Чистые контракты: in-memory ТОЛЬКО для dev/тестов ---------------
    # В кластере заменить на БД/Redis (задача A10). Передача None допустима
    # ЗДЕСЬ, потому что это ЯВНЫЕ реализации, а не «отсутствие порта».
    grant_store_impl = grant_store if grant_store is not None else InMemoryGrantStore()
    routes_impl = routes if routes is not None else InMemoryRouteRegistry()
    audit_impl = audit if audit is not None else InMemoryAdminAudit()

    ports = PluginAdminPorts(
        tenant_db=tenant_db,
        object_store=objects,
        artifact_registry=artifacts,
        grant_store=grant_store_impl,
        revocation=revocation,
        mint=mint,
        activation=activation,
        routes=routes_impl,
        audit=audit_impl,
        platform_admin=platform,
    )
    # Дополнительная страховка: у связки ровно 10 полей, все не None.
    _assert_all_ports_wired(ports)
    return ports


def build_plugin_admin_service(
    *,
    platform: PlatformAdminPort,
    tenant_db_service: Any,
    object_store_service: Any,
    bucket_admin: Any,
    artifact_registry: Any,
    revocation_source: Any,
    mint_secret: str | bytes,
    activator: Any,
    instance_resolver: Any,
    deployment_admin: Any,
    artifact_store: Any | None = None,
    grant_store: GrantStorePort | None = None,
    routes: RouteRegistryPort | None = None,
    audit: AuditPort | None = None,
    resolver_registrar: Any | None = None,
) -> PluginAdminService:
    """Удобная обёртка: собрать порты И сразу ``PluginAdminService``."""

    return PluginAdminService(
        build_plugin_admin_ports(
            platform=platform,
            tenant_db_service=tenant_db_service,
            object_store_service=object_store_service,
            bucket_admin=bucket_admin,
            artifact_registry=artifact_registry,
            revocation_source=revocation_source,
            mint_secret=mint_secret,
            activator=activator,
            instance_resolver=instance_resolver,
            deployment_admin=deployment_admin,
            artifact_store=artifact_store,
            grant_store=grant_store,
            routes=routes,
            audit=audit,
            resolver_registrar=resolver_registrar,
        )
    )


def _assert_all_ports_wired(ports: PluginAdminPorts) -> None:
    """Перепроверить 10 полей связки (защита от будущего «ослабления»)."""

    names = (
        "tenant_db",
        "object_store",
        "artifact_registry",
        "grant_store",
        "revocation",
        "mint",
        "activation",
        "routes",
        "audit",
        "platform_admin",
    )
    for name in names:
        require_wired(name, getattr(ports, name, None))


__all__ = [
    "AdapterDependencies",
    "build_plugin_admin_ports",
    "build_plugin_admin_service",
]
