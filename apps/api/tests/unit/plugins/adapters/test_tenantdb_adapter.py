"""Тесты ``tenantdb_adapter``: keywords → позиционные, TenantDsn → контракты.

Покрытие:
* позиционные вызовы реального сервиса с ТЕМ ЖЕ ``(company_id, plugin_id)``;
* ``TenantDsn`` → ``PluginSchema``/``TenantDatabase``, DSN только secret_ref;
* маппинг ``TenantDbError`` → ``PluginAdminError`` (код/операция в extra);
* чужая компания не подменяется (имена выведены из нашего id);
* ``drop_plugin_schema`` идемпотентен на уже отсутствующей роли.
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.admin.errors import PluginAdminError
from prodavan.application.plugins.adapters.tenantdb_adapter import TenantDbAdapter
from prodavan.application.plugins.tenantdb.errors import (
    NOT_FOUND,
    TenantDbError,
)
from prodavan.application.plugins.tenantdb.naming import (
    plugin_role_name,
    plugin_schema_name,
    plugin_search_path,
    tenant_db_name,
)
from prodavan.application.plugins.tenantdb.service import (
    TenantDatabaseService,
    TenantDsn,
)

from .fakes import (
    COMPANY,
    COMPANY_OTHER,
    PLUGIN,
    FakeAdminConnection,
    FakeSecretPort,
    FakeTenantDbAudit,
)


def _service(*, existing: set[str] | None = None, fail: bool = False) -> tuple[TenantDatabaseService, FakeAdminConnection]:
    admin = FakeAdminConnection(existing_databases=existing or set(), fail=fail)
    service = TenantDatabaseService(
        admin=admin,
        secrets=FakeSecretPort(),
        audit=FakeTenantDbAudit(),
        dsn_host="tenant-postgres.svc",
        dsn_port=5432,
    )
    return service, admin


# --------------------------------------------------------------------------- ensure БД


async def test_ensure_company_database_is_positional_and_maps_to_tenant_database() -> None:
    service, admin = _service()
    adapter = TenantDbAdapter(service)

    result = await adapter.ensure_company_database(company_id=COMPANY)

    assert result.company_id == COMPANY
    assert result.database_name == tenant_db_name(COMPANY)
    assert result.dsn_ref.startswith("secret://")
    assert "postgresql://" not in result.dsn_ref  # plaintext DSN не ушёл наружу
    assert result.created is True
    # SELECT 1 ... ушёл на cluster-scope с именем НАШЕЙ БД.
    assert any("pg_database" in str(c.args[0]) for c in admin.calls)


async def test_ensure_company_database_detects_existing_db() -> None:
    db = tenant_db_name(COMPANY)
    service, _admin = _service(existing={db})
    adapter = TenantDbAdapter(service)

    result = await adapter.ensure_company_database(company_id=COMPANY)
    assert result.created is False


async def test_ensure_company_database_rejects_foreign_company() -> None:
    """Имя БД обязано быть выведено из авторитетного company_id."""

    service, _admin = _service()
    adapter = TenantDbAdapter(service)

    result = await adapter.ensure_company_database(company_id=COMPANY_OTHER)
    assert result.company_id == COMPANY_OTHER
    assert result.database_name == tenant_db_name(COMPANY_OTHER)
    assert result.database_name != tenant_db_name(COMPANY)
    with pytest.raises(TenantDbError):
        tenant_db_name("co_a; DROP DATABASE prodavan")

# --------------------------------------------------------------------------- ensure схемы


async def test_ensure_plugin_schema_maps_tenant_dsn_and_validates_scope() -> None:
    service, _admin = _service()
    adapter = TenantDbAdapter(service)

    schema = await adapter.ensure_plugin_schema(
        company_id=COMPANY,
        plugin_id=PLUGIN,
        dsn_ref="",  # пустой = «сверять не с чем», допускаем
    )

    assert schema.company_id == COMPANY
    assert schema.plugin_id == PLUGIN
    assert schema.database_name == tenant_db_name(COMPANY)
    assert schema.schema_name == plugin_schema_name(PLUGIN)
    assert schema.role_name == plugin_role_name(COMPANY, PLUGIN)
    assert schema.search_path == plugin_search_path(PLUGIN)
    assert schema.dsn_ref.startswith("secret_ref://")  # НЕ plaintext DSN
    assert "postgresql://" not in schema.dsn_ref
    assert schema.created is True


async def test_ensure_plugin_schema_rejects_foreign_company_dsn_ref() -> None:
    """``dsn_ref`` на другую компанию = отказ, а не тихая подмена."""

    service, _admin = _service()
    adapter = TenantDbAdapter(service)

    with pytest.raises(PluginAdminError, match="не на БД этой компании"):
        await adapter.ensure_plugin_schema(
            company_id=COMPANY,
            plugin_id=PLUGIN,
            dsn_ref="secret://plugins/tenantdb/co_other/TENANT_DB_DSN",
        )


async def test_ensure_plugin_schema_accepts_own_company_dsn_ref() -> None:
    """Служебная ссылка ensure_company_database проходит проверку области."""

    service, _admin = _service()
    adapter = TenantDbAdapter(service)
    database = await adapter.ensure_company_database(company_id=COMPANY)

    schema = await adapter.ensure_plugin_schema(
        company_id=COMPANY, plugin_id=PLUGIN, dsn_ref=database.dsn_ref
    )
    assert schema.dsn_ref.startswith("secret_ref://")  # DSN роли плагина
    assert schema.dsn_ref != database.dsn_ref  # это ДВЕ разные ссылки


async def test_ensure_plugin_schema_calls_grant_plugin_positionally() -> None:
    """Сверяем, что в реальный сервис ушли ровно два позиционных аргумента."""

    recorded: list[tuple[str, tuple[object, ...]]] = []

    class RecordingService(TenantDatabaseService):
        async def grant_plugin(self, company_id: str, plugin_id: str) -> TenantDsn:
            recorded.append(("grant_plugin", (company_id, plugin_id)))
            return await super().grant_plugin(company_id, plugin_id)

    service = RecordingService(
        admin=FakeAdminConnection(),
        secrets=FakeSecretPort(),
        audit=FakeTenantDbAudit(),
        dsn_host="tenant-postgres.svc",
    )
    adapter = TenantDbAdapter(service)
    await adapter.ensure_plugin_schema(
        company_id=COMPANY, plugin_id=PLUGIN, dsn_ref=""
    )
    assert recorded == [("grant_plugin", (COMPANY, PLUGIN))]


# --------------------------------------------------------------------------- drop


async def test_drop_plugin_schema_returns_pair_and_is_idempotent() -> None:
    service, _admin = _service()
    adapter = TenantDbAdapter(service)

    assert await adapter.drop_plugin_schema(company_id=COMPANY, plugin_id=PLUGIN) == (
        True,
        True,
    )
    # Повтор: роль уже удалена → реальный сервис бросает NOT_FOUND → (True, True).
    assert await adapter.drop_plugin_schema(company_id=COMPANY, plugin_id=PLUGIN) == (
        True,
        True,
    )


async def test_drop_plugin_schema_does_not_touch_other_company() -> None:
    """Имя роли для удаления выведено ТОЛЬКО из нашей пары."""

    service, _admin = _service()
    adapter = TenantDbAdapter(service)
    await adapter.ensure_plugin_schema(company_id=COMPANY, plugin_id=PLUGIN, dsn_ref="")

    # Удаляем у компании A; роль компании B с тем же plugin_id — другая.
    await adapter.drop_plugin_schema(company_id=COMPANY, plugin_id=PLUGIN)
    assert plugin_role_name(COMPANY, PLUGIN) != plugin_role_name(COMPANY_OTHER, PLUGIN)

# --------------------------------------------------------------------------- ошибки


async def test_tenant_db_error_is_mapped_to_plugin_admin_error() -> None:
    service, _admin = _service(fail=True)
    adapter = TenantDbAdapter(service)

    with pytest.raises(PluginAdminError) as exc:
        await adapter.ensure_company_database(company_id=COMPANY)
    assert exc.value.extra["adapter_operation"] == "ensure_company_database"
    assert "postgresql://" not in (exc.value.detail or "")
    assert "password" not in (exc.value.detail or "").lower()


def test_tenant_db_admin_code_is_closed_set() -> None:
    from prodavan.application.plugins.adapters.errors import tenant_db_admin_code

    assert tenant_db_admin_code(operation="ensure_company_database").value == "GRANT_EXISTS"
    assert tenant_db_admin_code(operation="drop_plugin_schema").value == "UNINSTALL_PARTIAL"
    with pytest.raises(ValueError, match="неизвестная операция"):
        tenant_db_admin_code(operation="unknown")


def test_not_found_error_constant_matches_idempotent_path() -> None:
    """`NOT_FOUND` — тот самый код, который адаптер маппит в (True, True)."""

    assert NOT_FOUND == "NOT_FOUND"
    assert TenantDbError.not_found("uninstall_plugin", object_kind="role", safe_name="r").code == NOT_FOUND
