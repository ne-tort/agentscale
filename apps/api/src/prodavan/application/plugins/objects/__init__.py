"""Объектная capability плагинов (архитектура v4 §5.4, F3, задача 06 плана).

Публичный контракт:
  * ``policy``   — генератор IAM/session-политик, явный префикс без policy
                   variables (``${aws:username}`` / ``${jwt:*}`` — опция MinIO,
                   требующая подтверждения официальной документацией);
  * ``ports``    — typed Protocol-порты (BucketAdmin / Sts / Presign / Audit)
                   и валидатор TTL 60..3600;
  * ``service``  — ``PluginObjectStore``: grant / short-lived scoped creds /
                   scoped presigned / list / revoke / uninstall + аудит
                   ``plugin_object_access``;
  * ``errors``   — fail-closed коды ошибок.

Ни один контракт не передаёт ``bytes`` / payload через API: плагин работает с
MinIO :9000 через short-lived scoped creds или presigned URL напрямую.
``TenantObjectsService`` (``application/tenant_infra/objects_service.py``)
остаётся фасадом аудита/квот/листинга, но НЕ единственной границей — граница
обеспечивается IAM-политикой bucket-per-tenant и жёстким префиксом.
"""

from __future__ import annotations

from prodavan.application.plugins.objects.errors import (
    AuditUnavailable,
    BucketUnavailable,
    InvalidObjectKey,
    InvalidObjectMethod,
    ObjectAccessDenied,
    PluginObjectError,
    StsUnavailable,
)
from prodavan.application.plugins.objects.policy import (
    OBJECT_ACTIONS,
    POLICY_VERSION,
    bucket_name,
    bucket_policy,
    module_prefix,
    session_policy,
)
from prodavan.application.plugins.objects.ports import (
    DEFAULT_TTL_SECONDS,
    MAX_TTL_SECONDS,
    MIN_TTL_SECONDS,
    AuditPort,
    BucketAdminPort,
    InvalidTtl,
    ObjectInfo,
    ObjectMethod,
    PresignPort,
    ScopedCredentials,
    StsPort,
    validate_ttl,
)
from prodavan.application.plugins.objects.service import (
    AUDIT_EVENT,
    PluginObjectStore,
)

__all__ = [
    # policy
    "POLICY_VERSION",
    "OBJECT_ACTIONS",
    "bucket_name",
    "module_prefix",
    "bucket_policy",
    "session_policy",
    # ports / типы
    "MIN_TTL_SECONDS",
    "MAX_TTL_SECONDS",
    "DEFAULT_TTL_SECONDS",
    "validate_ttl",
    "InvalidTtl",
    "ObjectMethod",
    "ScopedCredentials",
    "ObjectInfo",
    "BucketAdminPort",
    "StsPort",
    "PresignPort",
    "AuditPort",
    # service
    "AUDIT_EVENT",
    "PluginObjectStore",
    # errors
    "PluginObjectError",
    "ObjectAccessDenied",
    "InvalidObjectKey",
    "InvalidObjectMethod",
    "BucketUnavailable",
    "StsUnavailable",
    "AuditUnavailable",
]
