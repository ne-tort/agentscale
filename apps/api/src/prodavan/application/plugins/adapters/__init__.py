"""Тонкие адаптеры: ``PluginAdminPorts`` → реальные сервисы плагинов.

Этот пакет — ИСКЛЮЧИТЕЛЬНО слой стыка (задача «миграция/адаптеры»). Он НЕ
редактирует чужие каталоги (``identity``, ``tenantdb``, ``objects``,
``artifacts``, ``activation``, ``gateway``, ``admin``), не читает ``settings``,
не открывает соединений и не содержит «заглушек успеха».

Что внутри
----------
===========================  =====================================================
Модуль                       Стык
===========================  =====================================================
``identity_adapter``         admin.RevocationPort/MintPort → identity.sync-ядро
``tenantdb_adapter``         admin.TenantDbPort → TenantDatabaseService
``objects_adapter``          admin.ObjectStorePort → PluginObjectStore/BucketAdminPort
``artifacts_adapter``        admin.ArtifactRegistryPort → PluginArtifactRegistry
``activation_adapter``       admin.ActivationPort И gateway.ActivationPort →
                             PluginActivator + k8s DeploymentAdminPort
``stores``                   in-memory реализации чистых контрактов (A10!)
``build``                    сборка всех 10 портов, fail-closed
``errors``                   маппинг ошибок чужих зон → PluginAdminError
===========================  =====================================================

Правила, обязательные для всех адаптеров:

* **Fail-closed.** Отсутствие реализации, неподтверждённая запись, чужой
  ``company_id``, непроверенный digest, битый ответ соседнего сервиса —
  ЯВНАЯ ошибка, а не молчаливый успех.
* **Изоляция компаний.** Ключ любой операции — авторитетная пара
  ``(company_id, plugin_id)``; ``company_id`` никогда не берётся из тела
  ответа соседнего сервиса и никогда не подменяется «соседней» компанией.
* **Коллизия имён.** ``MintPort`` (admin vs activation), ``ActivationPort``
  (admin vs gateway), ``RevocationPort`` (admin vs identity), ``AuditPort``
  (admin vs tenantdb vs objects vs gateway) — РАЗНЫЕ протоколы. Импорты
  выполнены с алиасами; см. ``test_mint_port_alias_collision.py``.
* **PUBLISHED только из проверенного digest.** См. ``artifacts_adapter``:
  статус реестра маппится в ``ArtifactPublicationStatus.PUBLISHED`` только
  после сверки ``sha256:<64 hex>`` и подтверждённой публикации, НЕ из тега.
* **Без переноса данных.** Ни snapshot, ни dual-read/dual-write, ни migrate
  (§8 / §13.1, данные dev эфемерны).

ОГРАНИЧЕНИЯ (читать родителю перед F11)
---------------------------------------
1. ``InMemoryGrantStore`` / ``InMemoryRouteRegistry`` / ``InMemoryAdminAudit`` /
   ``InMemoryInstanceResolver`` / in-memory реестр установок артефактов и
   ``StaticPlatformAdmin`` — ТОЛЬКО для тестов и однопроцессного dev. Для
   кластера нужны реализации поверх БД/Redis/k8s: **задача A10**
   (``CapabilityGrant``/``PluginInstance`` + Alembic-ревизия), Redis с CAS для
   отзывов/аудита/маршрутов, k8s-клиент для ``DeploymentAdminPort`` и
   ``BucketAdminPort``/``StsPort``, MinIO/GHCR для объектов и артефактов,
   настоящая PlatformAdmin-auth (OIDC/JWKS).
2. У задачи 12 ЗАКРЫТЫЙ набор кодов ``PluginAdminError`` (5 штук); отдельного
   «инфраструктурный отказ адаптера» нет. Исходный код чужой ошибки
   сохраняется в ``extra``, а сам отказ проецируется в ближайший lifecycle-код.
   Рекомендуется добавить ``PLUGIN_ADMIN_INFRA_FAILED`` в задаче 12.
3. ``TenantDatabaseService.uninstall_plugin`` бросает ``NOT_FOUND`` при уже
   удалённой роли; адаптер маппит его в идемпотентный ``(True, True)``.
4. Адаптер артефактов НЕ удаляет глобальный immutable digest из GHCR:
   ``unlink_installation`` снимает только tenant-scoped установку.

Тесты написаны, НО НЕ ЗАПУСКАЛИСЬ (verification freeze плана миграции).
"""

from __future__ import annotations

from .activation_adapter import (
    ActivatorMintPort,
    AdminActivationAdapter,
    AdminActivationPort,
    AdminMintPort,
    DeploymentAdminPort,
    GatewayActivationAdapter,
    GatewayActivationPort,
    InMemoryInstanceResolver,
    InstanceIdResolver,
)
from .artifacts_adapter import ArtifactRegistryAdapter, DigestLookupPort
from .build import (
    AdapterDependencies,
    build_plugin_admin_ports,
    build_plugin_admin_service,
)
from .errors import (
    map_tenant_db_error,
    map_unexpected_error,
    require_wired,
    tenant_db_admin_code,
)
from .identity_adapter import (
    AdminMintPort as IdentityAdminMintPort,
)
from .identity_adapter import (
    AdminRevocationPort,
    IdentityMintAdapter,
    IdentityRevocationAdapter,
    IdentityRevocationPort,
    SyncRevocationSource,
)
from .objects_adapter import ObjectStoreAdapter
from .stores import (
    InMemoryAdminAudit,
    InMemoryGrantStore,
    InMemoryRouteRegistry,
    StaticPlatformAdmin,
)
from .tenantdb_adapter import TenantDbAdapter

__all__ = [
    # Сборка.
    "AdapterDependencies",
    "build_plugin_admin_ports",
    "build_plugin_admin_service",
    # Адаптеры.
    "AdminActivationAdapter",
    "ArtifactRegistryAdapter",
    "GatewayActivationAdapter",
    "IdentityMintAdapter",
    "IdentityRevocationAdapter",
    "ObjectStoreAdapter",
    "TenantDbAdapter",
    # Порты / резолверы.
    "DeploymentAdminPort",
    "DigestLookupPort",
    "InMemoryInstanceResolver",
    "InstanceIdResolver",
    "SyncRevocationSource",
    # Алиасы (коллизия имён).
    "ActivatorMintPort",
    "AdminActivationPort",
    "AdminMintPort",
    "AdminRevocationPort",
    "GatewayActivationPort",
    "IdentityAdminMintPort",
    "IdentityRevocationPort",
    # Хранилища (A10!).
    "InMemoryAdminAudit",
    "InMemoryGrantStore",
    "InMemoryRouteRegistry",
    "StaticPlatformAdmin",
    # Ошибки.
    "map_tenant_db_error",
    "map_unexpected_error",
    "require_wired",
    "tenant_db_admin_code",
]
