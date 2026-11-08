"""Единая точка перевода ошибок чужих зон в ``PluginAdminError``.

Адаптер — это граница: исключения соседних каталогов (``TenantDbError``,
``PluginObjectError``, ``ArtifactError``, ``ActivationError``, ...) не должны
«протекать» в Admin API в первозданном виде, но и не должны превращаться в
молчаливый успех. Любая неопределённость = ``PluginAdminError`` (fail-closed).

ОГРАНИЧЕНИЕ (важно для родителя, F11): у задачи 12 **закрытый** набор кодов
``PluginAdminErrorCode`` — ровно 5 штук, и отдельного «инфраструктурный отказ
адаптера» в нём нет. Поэтому исходный код чужой ошибки НЕ теряется, а кладётся
в ``extra``; сам отказ проецируется в ближайший lifecycle-код операции.
Рекомендация родителю: завести ``PLUGIN_ADMIN_INFRA_FAILED`` в задаче 12,
чтобы клиент не принимал инфраструктурный 503 за «уже установлено».
"""

from __future__ import annotations

from typing import Any

from prodavan.application.plugins.admin.errors import (
    PluginAdminError,
    PluginAdminErrorCode,
)
from prodavan.application.plugins.tenantdb.errors import TenantDbError

#: Ближайший lifecycle-код для каждой операции admin-порта tenant-БД.
#: ``drop_plugin_schema`` вызывается из uninstall → UNINSTALL_PARTIAL;
#: остальные — из grant → GRANT_EXISTS (см. ограничение выше).
_TENANT_DB_ADMIN_CODES: dict[str, PluginAdminErrorCode] = {
    "ensure_company_database": PluginAdminErrorCode.GRANT_EXISTS,
    "ensure_plugin_schema": PluginAdminErrorCode.GRANT_EXISTS,
    "drop_plugin_schema": PluginAdminErrorCode.UNINSTALL_PARTIAL,
}


def tenant_db_admin_code(*, operation: str) -> PluginAdminErrorCode:
    """Вернуть ближайший admin-код для операции (fail-closed на неизвестной)."""

    code = _TENANT_DB_ADMIN_CODES.get(operation)
    if code is None:
        raise ValueError(f"неизвестная операция TenantDbAdapter: {operation!r}")
    return code


def map_tenant_db_error(
    exc: TenantDbError,
    *,
    operation: str,
    reason: str,
) -> PluginAdminError:
    """``TenantDbError`` → ``PluginAdminError`` без утечки DSN/пароля/raw SQL.

    ``TenantDbError.detail`` по построению содержит только код операции и уже
    нормализованное безопасное имя (см. ``tenantdb/errors.py``), поэтому его
    можно переиспользовать. Исходный ``code``/``operation`` сохраняются в
    ``extra`` для диагностики; ничего, пришедшего извне, в ``extra`` не кладём.
    """

    return PluginAdminError(
        code=tenant_db_admin_code(operation=operation),
        detail=f"{reason}: {exc.detail}",
        status=exc.http_status,
        extra={
            "tenant_db_code": exc.code,
            "tenant_db_operation": exc.operation,
            "adapter_operation": operation,
        },
    )


def map_unexpected_error(exc: Exception, *, operation: str, reason: str) -> PluginAdminError:
    """Непредвиденное исключение адаптера → явный отказ, а не «успех по умолчанию».

    В ``detail`` НЕ подставляем ``str(exc)``: там теоретически может оказаться
    DSN, SQL с параметрами или токен. Только тип исключения (безопасная строка).
    Для неизвестной операции используем нейтральный ``UNINSTALL_PARTIAL`` —
    это тоже отказ, а не «успех», так что fail-closed не нарушается.
    """

    code = _TENANT_DB_ADMIN_CODES.get(
        operation, PluginAdminErrorCode.UNINSTALL_PARTIAL
    )
    return PluginAdminError(
        code=code,
        detail=f"{reason}: {type(exc).__name__}",
        status=503,
        extra={"adapter_operation": operation},
    )


def require_wired(name: str, value: Any) -> Any:
    """Проверить, что реализация порта реально передана, а не ``None``/пустышка.

    Используется ``build_plugin_admin_ports``: отсутствие ЛЮБОЙ из 10 реализаций
    = ``ValueError`` с именем порта, а не заглушка, возвращающая «успех».
    """

    if value is None:
        raise ValueError(
            f"PluginAdminPorts: реализация {name!r} не подключена "
            "(fail-closed: отсутствие интеграции = явная ошибка)"
        )
    return value


__all__ = [
    "map_tenant_db_error",
    "map_unexpected_error",
    "require_wired",
    "tenant_db_admin_code",
]
