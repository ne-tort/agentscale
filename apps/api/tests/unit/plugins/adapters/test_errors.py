"""Тесты ``errors.py``: маппинг ошибок чужих зон → PluginAdminError."""

from __future__ import annotations

import pytest

from prodavan.application.plugins.admin.errors import (
    PluginAdminError,
    PluginAdminErrorCode,
)
from prodavan.application.plugins.adapters.errors import (
    map_tenant_db_error,
    map_unexpected_error,
    require_wired,
    tenant_db_admin_code,
)
from prodavan.application.plugins.tenantdb.errors import (
    ADMIN_UNAVAILABLE,
    TenantDbError,
)


def test_tenant_db_error_mapping_keeps_original_code_without_secrets() -> None:
    exc = TenantDbError.admin_unavailable("grant_plugin", reason="execute failed")
    mapped = map_tenant_db_error(
        exc, operation="ensure_plugin_schema", reason="роль/схема не выданы"
    )

    assert isinstance(mapped, PluginAdminError)
    assert mapped.code == PluginAdminErrorCode.GRANT_EXISTS
    assert mapped.extra["tenant_db_code"] == ADMIN_UNAVAILABLE
    assert mapped.extra["tenant_db_operation"] == "grant_plugin"
    assert mapped.extra["adapter_operation"] == "ensure_plugin_schema"
    assert "postgresql" not in (mapped.detail or "").lower()
    assert "password" not in (mapped.detail or "").lower()


def test_unexpected_error_does_not_leak_exception_text() -> None:
    exc = RuntimeError("postgresql://user:password@host/db SELECT 1")
    mapped = map_unexpected_error(
        exc, operation="ensure_company_database", reason="БД недоступна"
    )

    assert mapped.status == 503
    assert "RuntimeError" in (mapped.detail or "")
    assert "postgresql://" not in (mapped.detail or "")
    assert "password" not in (mapped.detail or "")


def test_tenant_db_admin_code_is_closed() -> None:
    assert tenant_db_admin_code(operation="ensure_company_database").value == "GRANT_EXISTS"
    assert tenant_db_admin_code(operation="ensure_plugin_schema").value == "GRANT_EXISTS"
    assert tenant_db_admin_code(operation="drop_plugin_schema").value == "UNINSTALL_PARTIAL"
    with pytest.raises(ValueError, match="неизвестная операция"):
        tenant_db_admin_code(operation="nope")


def test_require_wired_is_fail_closed() -> None:
    with pytest.raises(ValueError, match="fail-closed"):
        require_wired("some_port", None)
    marker = object()
    assert require_wired("some_port", marker) is marker
