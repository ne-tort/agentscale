"""Tenant DB lifecycle для плагинов (F3, задача 05).

Публичный контракт пакета: чистые имена, ошибки и сервис с портами.
Генератор DDL (``sql``) считается внутренним: AdminConnectionPort получает
уже собранные ``DdlStatement``, но внешний код может использовать его для
интеграционных проверок (§11 критерии 1, 2, 5, 15).

Импорты намеренно явные и без побочных эффектов: пакет НЕ читает
``settings``, не создаёт движок SQLAlchemy, не открывает соединений.
Всё подключается родителем в F11 через аргументы ``TenantDatabaseService``.
"""

from __future__ import annotations

from prodavan.application.plugins.tenantdb.errors import (
    ADMIN_UNAVAILABLE,
    ALREADY_EXISTS,
    ALL_CODES,
    INVALID_IDENTIFIER,
    NOT_FOUND,
    PRIVILEGE_DENIED,
    TenantDbError,
)
from prodavan.application.plugins.tenantdb.naming import (
    MAX_COMPANY_ID_BYTES,
    MAX_PLUGIN_ID_BYTES,
    MAX_ROLE_COMPANY_PLUGIN_BYTES,
    PG_MAX_IDENTIFIER_BYTES,
    assert_safe_identifier,
    bucket_name,
    normalize_identifier,
    plugin_role_name,
    plugin_schema_name,
    plugin_search_path,
    tenant_db_name,
)
from prodavan.application.plugins.tenantdb.service import (
    AdminConnectionPort,
    AdminScope,
    AdminTarget,
    AuditPort,
    ExecuteResult,
    SecretPort,
    TenantDatabaseService,
    TenantDsn,
)

__all__ = [
    # Ошибки и коды
    "TenantDbError",
    "ALL_CODES",
    "INVALID_IDENTIFIER",
    "ADMIN_UNAVAILABLE",
    "ALREADY_EXISTS",
    "NOT_FOUND",
    "PRIVILEGE_DENIED",
    # Имена
    "tenant_db_name",
    "plugin_role_name",
    "plugin_schema_name",
    "plugin_search_path",
    "bucket_name",
    "normalize_identifier",
    "assert_safe_identifier",
    "PG_MAX_IDENTIFIER_BYTES",
    "MAX_COMPANY_ID_BYTES",
    "MAX_PLUGIN_ID_BYTES",
    "MAX_ROLE_COMPANY_PLUGIN_BYTES",
    # Сервис и порты
    "TenantDatabaseService",
    "TenantDsn",
    "AdminConnectionPort",
    "AdminTarget",
    "AdminScope",
    "ExecuteResult",
    "SecretPort",
    "AuditPort",
]
