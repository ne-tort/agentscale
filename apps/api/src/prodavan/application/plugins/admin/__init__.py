"""F3-admin: Admin API lifecycle плагинов (план миграции, задача 12).

Публичный контракт зоны:

* ``contracts`` — типизированные контракты (без FastAPI/Pydantic/БД): реестры
  capability/квот, ``EgressPolicy``/``QuotaSet``/``CapabilitySet``,
  ``CapabilityGrantRecord``, ``MintRequest``/``IssuedPluginClaims``, receipts,
  ``AdminAuditEvent``;
* ``errors``    — ``PluginAdminError`` + коды ARTIFACT_NOT_PUBLISHED, GRANT_EXISTS,
  GRANT_NOT_FOUND, REVOKE_FAILED, UNINSTALL_PARTIAL;
* ``ports``     — ``Protocol`` для задач 02/05/06/07/09 + ``GrantStorePort``,
  ``RouteRegistryPort``, ``MintPort``, ``AuditPort``, ``PlatformAdminPort`` и
  неизменяемая связка ``PluginAdminPorts``;
* ``service``   — ``PluginAdminService``: grant / revoke / uninstall / wake / list /
  set_enabled_modules.

Интеграция (выполняет родитель в F11, не эта зона):

.. code-block:: python

    from prodavan.application.plugins.admin import PluginAdminPorts, PluginAdminService

    ports = PluginAdminPorts(
        tenant_db=...,            # адаптер к tenantdb.TenantDatabaseService
        object_store=...,         # адаптер к objects.*
        artifact_registry=...,    # адаптер к artifacts.PluginArtifactRegistry
        instance_store=...,       # SQL PluginInstanceRow (durable saga)
        grant_store=...,          # БД-репозиторий CapabilityGrant + фича-флага
        revocation=...,           # адаптер к identity.InMemoryRevocation / БД
        mint=...,                 # адаптер к identity.mint_plugin_claims
        activation=...,           # адаптер к activation.PluginActivator
        routes=...,               # реестр маршрутов/egress-правил
        audit=...,                # append-only sink
        platform_admin=...,       # реальная PlatformAdmin-auth
    )
    app.state.plugin_admin_service = PluginAdminService(ports)
    app.state.plugin_admin_ports = ports

Все методы fail-closed и идемпотентные. Переноса данных, snapshot, dual-read/dual-write
и операции переноса в плагин здесь нет по решению §8 / §13.1 дизайна v4 (dev, данные
эфемерны).
"""

from __future__ import annotations

from .contracts import (
    ALLOWED_CAPABILITIES,
    PLUGIN_CLAIMS_TTL_SECONDS,
    PLUGIN_OBJECT_PREFIX,
    PLUGIN_SCHEMA_PREFIX,
    QUOTA_KEYS,
    TENANT_BUCKET_PREFIX,
    TENANT_DATABASE_PREFIX,
    AdminAuditEvent,
    AdminDecision,
    AdminIdentity,
    AdminOperation,
    ArtifactPublicationStatus,
    ArtifactRecord,
    CapabilityGrantRecord,
    CapabilitySet,
    EgressMode,
    EgressPolicy,
    EnabledModulesReceipt,
    GrantReceipt,
    GrantStatus,
    InstalledPlugin,
    IssuedPluginClaims,
    MintRequest,
    ObjectStoreBinding,
    PluginInstanceRecord,
    PluginInstanceStatus,
    PluginListSnapshot,
    PluginRuntimeStatus,
    PluginSchema,
    QuotaSet,
    RevokeReceipt,
    RouteRegistration,
    TenantDatabase,
    UninstallReceipt,
    WakeReceipt,
    WakeResult,
)
from .errors import (
    PluginAdminError,
    PluginAdminErrorCode,
    artifact_not_published,
    grant_exists,
    grant_not_found,
    revoke_failed,
    uninstall_partial,
)
from .ports import (
    ActivationPort,
    ArtifactRegistryPort,
    AuditPort,
    GrantStorePort,
    InstanceStorePort,
    MintPort,
    ObjectStorePort,
    PlatformAdminPort,
    PluginAdminPorts,
    RevocationPort,
    RouteRegistryPort,
    TenantDbPort,
)
from .service import PluginAdminService

__all__ = [
    # Реестры / константы
    "ALLOWED_CAPABILITIES",
    "PLUGIN_CLAIMS_TTL_SECONDS",
    "PLUGIN_OBJECT_PREFIX",
    "PLUGIN_SCHEMA_PREFIX",
    "QUOTA_KEYS",
    "TENANT_BUCKET_PREFIX",
    "TENANT_DATABASE_PREFIX",
    # Контракты
    "AdminAuditEvent",
    "AdminDecision",
    "AdminIdentity",
    "AdminOperation",
    "ArtifactPublicationStatus",
    "ArtifactRecord",
    "CapabilityGrantRecord",
    "CapabilitySet",
    "EgressMode",
    "EgressPolicy",
    "EnabledModulesReceipt",
    "GrantReceipt",
    "GrantStatus",
    "InstalledPlugin",
    "IssuedPluginClaims",
    "MintRequest",
    "ObjectStoreBinding",
    "PluginInstanceRecord",
    "PluginInstanceStatus",
    "PluginListSnapshot",
    "PluginRuntimeStatus",
    "PluginSchema",
    "QuotaSet",
    "RevokeReceipt",
    "RouteRegistration",
    "TenantDatabase",
    "UninstallReceipt",
    "WakeReceipt",
    "WakeResult",
    # Ошибки
    "PluginAdminError",
    "PluginAdminErrorCode",
    "artifact_not_published",
    "grant_exists",
    "grant_not_found",
    "revoke_failed",
    "uninstall_partial",
    # Порты
    "ActivationPort",
    "ArtifactRegistryPort",
    "AuditPort",
    "GrantStorePort",
    "InstanceStorePort",
    "MintPort",
    "ObjectStorePort",
    "PlatformAdminPort",
    "PluginAdminPorts",
    "RevocationPort",
    "RouteRegistryPort",
    "TenantDbPort",
    # Сервис
    "PluginAdminService",
]
