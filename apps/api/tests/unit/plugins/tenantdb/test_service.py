"""Тесты ``TenantDatabaseService`` (§5.5, §11 критерии 1/2/3/4/5/15).

Кейсы задания F3:
  * повторный ``ensure_company_database`` НЕ шлёт CREATE DATABASE второй раз;
  * ``grant_plugin`` идемпотентен по DDL, но ротирует DSN;
  * DSN не содержит пароля в логах/аудите (только secret_ref);
  * fail-closed: нет админского соединения → ошибка, не «успех»;
  * uninstall не трогает чужую БД;
  * revoke сохраняет БД/схему;
  * все операции через порты (Protocol) и admin target.

Тесты НЕ запускаются (верификационный фриз), все зависимости — in-memory fakes.
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.tenantdb import (
    ADMIN_UNAVAILABLE,
    ALREADY_EXISTS,
    ALL_CODES,
    INVALID_IDENTIFIER,
    NOT_FOUND,
    PRIVILEGE_DENIED,
    TenantDbError,
    plugin_role_name,
    plugin_schema_name,
    tenant_db_name,
)
from prodavan.application.plugins.tenantdb.service import AdminTarget, ExecuteResult, TenantDatabaseService

from .conftest import FakeAdminConnection, FakeAudit, FakeSecretStore, make_service

COMPANY = "co_123"
PLUGIN = "mod_equipment"
DB = tenant_db_name(COMPANY)
ROLE = plugin_role_name(COMPANY, PLUGIN)
SCHEMA = plugin_schema_name(PLUGIN)


# region ensure_company_database — идемпотентность
async def test_ensure_company_database_creates_once() -> None:
    service, admin, _secrets, audit = make_service()
    result = await service.ensure_company_database(COMPANY)
    assert result == DB
    assert DB in admin.databases
    assert admin.count("CREATE DATABASE") == 1
    assert admin.count("SELECT 1 FROM pg_database") == 1


async def test_ensure_company_database_second_call_does_not_recreate() -> None:
    """Главный кейс: повторный вызов НЕ шлёт CREATE DATABASE второй раз."""
    service, admin, _secrets, _audit = make_service()
    await service.ensure_company_database(COMPANY)
    await service.ensure_company_database(COMPANY)
    await service.ensure_company_database(COMPANY)
    assert admin.count("CREATE DATABASE") == 1
    assert admin.count("SELECT 1 FROM pg_database") == 3
    assert admin.databases == {DB}


async def test_ensure_company_database_uses_cluster_target() -> None:
    service, admin, _secrets, _audit = make_service()
    await service.ensure_company_database(COMPANY)
    for _text, _params, target in admin.statements:
        assert target == AdminTarget(scope="cluster")
# endregion


# region grant_plugin — DSN, roles, schema, идемпотентность
async def test_grant_plugin_returns_secret_ref_not_plaintext_dsn() -> None:
    """§5.5: плагин получает ``secret_ref``, plaintext в манифесте запрещён."""
    service, admin, secrets, audit = make_service()
    result = await service.grant_plugin(COMPANY, PLUGIN)

    assert result.secret_ref.startswith("vault://")
    assert result.database == DB
    assert result.role == ROLE
    assert result.schema == SCHEMA
    assert result.bucket == f"tenant-{COMPANY}"
    assert result.search_path == SCHEMA
    assert not result.rotated

    # В SecretStore лежит настоящий DSN с паролем...
    stored_dsn = next(iter(secrets.values.values()))
    assert stored_dsn.startswith("postgresql://")
    assert ":" in stored_dsn.split("@")[0]  # user:password
    assert f"/{DB}?" in stored_dsn
    assert "sslmode=require" in stored_dsn
    # ...но НИ В ОДНОМ ответе/аудите/логе его нет.
    assert stored_dsn not in repr(result)
    assert stored_dsn not in str(result)
    assert stored_dsn not in audit.all_payload_text()
    assert "password" not in audit.all_payload_text().lower()
    assert "postgresql://" not in audit.all_payload_text()
    # as_ref — тоже только ссылка.
    assert result.as_ref() == {"TENANT_DB_DSN": f"secret_ref:{result.secret_ref}"}
    assert "postgresql://" not in repr(result.as_ref())


async def test_grant_plugin_password_not_leaked_via_errors_or_audit() -> None:
    """Пароль роли не попадает в AuditPort и не в ``TenantDsn``."""
    service, _admin, secrets, audit = make_service()
    result = await service.grant_plugin(COMPANY, PLUGIN)
    dsn = next(iter(secrets.values.values()))
    password = dsn.split("@")[0].split(":", 1)[1]
    assert password
    assert password not in audit.all_payload_text()
    assert password not in repr(result)
    assert password not in str(result)
    # Ни один statement не попал в аудит: там нет raw SQL/параметров.
    assert "params" not in audit.all_payload_text()
    assert "CREATE ROLE" not in audit.all_payload_text()


async def test_grant_plugin_creates_role_schema_and_search_path() -> None:
    service, admin, _secrets, _audit = make_service()
    await service.grant_plugin(COMPANY, PLUGIN)

    assert DB in admin.databases
    assert ROLE in admin.roles
    assert SCHEMA in admin.schemas[DB]
    assert admin.count("CREATE ROLE") == 1
    assert admin.count("CREATE SCHEMA") == 1
    assert admin.count("GRANT CONNECT") == 1
    assert admin.count("ALTER ROLE") >= 3

    create_role = admin.texts(prefix="CREATE ROLE")[0]
    for flag in ("NOSUPERUSER", "NOCREATEDB", "NOCREATEROLE", "NOREPLICATION", "NOBYPASSRLS"):
        assert flag in create_role
    assert "CONNECTION LIMIT 10" in create_role

    # CREATE SCHEMA с owner=роль плагина выполняется в БД компании.
    schema_stmts = [s for s in admin.statements if s[0].startswith("CREATE SCHEMA")]
    assert len(schema_stmts) == 1
    assert f'AUTHORIZATION "{ROLE}"' in schema_stmts[0][0]
    assert schema_stmts[0][2] == AdminTarget(scope="database", database=DB)

    # search_path без версии плагина.
    sp = [t for t in admin.texts(prefix="ALTER ROLE") if "search_path" in t]
    assert len(sp) == 1
    assert f"search_path = '{SCHEMA}'" in sp[0]
    assert "v1" not in sp[0] and "v2" not in sp[0]


async def test_grant_plugin_second_call_is_idempotent_but_rotates_dsn() -> None:
    """Повторный grant: CREATE DATABASE/ROLE/SCHEMA — по разу, DSN ротируется."""
    service, admin, secrets, audit = make_service()
    first = await service.grant_plugin(COMPANY, PLUGIN)
    second = await service.grant_plugin(COMPANY, PLUGIN)

    assert admin.count("CREATE DATABASE") == 1
    assert admin.count("CREATE ROLE") == 1
    assert admin.count("CREATE SCHEMA") == 1
    assert admin.count("ALTER ROLE") >= 6  # 3 + 3 (password/login/search_path/таймауты)
    assert second.rotated is True
    assert second.secret_ref != first.secret_ref
    assert len(secrets.rotates) == 1
    # Ротация = ALTER ROLE .. PASSWORD, а не второй CREATE ROLE.
    assert admin.count("ALTER ROLE") >= 6
    assert any("PASSWORD" in t for t in admin.texts(prefix="ALTER ROLE"))
    assert "rotated" in audit.all_payload_text()


async def test_grant_plugin_after_revoke_restores_login() -> None:
    """re-grant после revoke снимает NOLOGIN."""
    service, admin, _secrets, _audit = make_service()
    await service.grant_plugin(COMPANY, PLUGIN)
    await service.revoke_plugin(COMPANY, PLUGIN)
    await service.grant_plugin(COMPANY, PLUGIN)
    assert any("LOGIN" in t and "NOLOGIN" not in t for t in admin.texts(prefix="ALTER ROLE"))
    assert admin.count("CREATE ROLE") == 1  # всё ещё один раз


async def test_grant_plugin_validates_ids_before_any_ddl() -> None:
    """Невалидный id → ошибка ДО единого обращения к админскому порту."""
    service, admin, _secrets, _audit = make_service()
    with pytest.raises(TenantDbError) as ei:
        await service.grant_plugin("co_123; DROP DATABASE prodavan", PLUGIN)
    assert ei.value.code == INVALID_IDENTIFIER
    assert admin.statements == []
    with pytest.raises(TenantDbError):
        await service.grant_plugin(COMPANY, "mod; DROP SCHEMA public")
    assert admin.statements == []
# endregion


# region Fail-closed
async def test_no_admin_connection_is_error_not_success() -> None:
    """Нет админского соединения → ADMIN_UNAVAILABLE, не «успех»."""
    service = TenantDatabaseService(
        admin=None,
        secrets=FakeSecretStore(),
        audit=FakeAudit(),
        dsn_host="tenant-postgres",
    )
    with pytest.raises(TenantDbError) as ei:
        await service.ensure_company_database(COMPANY)
    assert ei.value.code == ADMIN_UNAVAILABLE
    assert ei.value.http_status == 503


async def test_missing_secret_store_is_error() -> None:
    service = TenantDatabaseService(
        admin=FakeAdminConnection(),
        secrets=None,
        audit=FakeAudit(),
        dsn_host="tenant-postgres",
    )
    with pytest.raises(TenantDbError) as ei:
        await service.grant_plugin(COMPANY, PLUGIN)
    assert ei.value.code == ADMIN_UNAVAILABLE


async def test_missing_audit_is_error() -> None:
    service = TenantDatabaseService(
        admin=FakeAdminConnection(),
        secrets=FakeSecretStore(),
        audit=None,
        dsn_host="tenant-postgres",
    )
    with pytest.raises(TenantDbError) as ei:
        await service.ensure_company_database(COMPANY)
    assert ei.value.code == ADMIN_UNAVAILABLE


async def test_driver_failure_is_fail_closed_and_text_not_leaked() -> None:
    """Ошибка драйвера → ADMIN_UNAVAILABLE; её текст (с DSN) не прокидывается."""
    admin = FakeAdminConnection()
    admin.fail_next = True
    service, _admin, _secrets, _audit = make_service(admin=admin)
    with pytest.raises(TenantDbError) as ei:
        await service.ensure_company_database(COMPANY)
    assert ei.value.code == ADMIN_UNAVAILABLE
    assert "simulated driver failure" not in str(ei.value)
    assert "postgresql" not in str(ei.value)


async def test_sqlstate_42501_maps_to_privilege_denied() -> None:
    """PostgreSQL 42501 → PRIVILEGE_DENIED, а не ADMIN_UNAVAILABLE."""
    admin2 = FakeAdminConnection()
    admin2.privilege_denied_next = True
    service2, _a2, _s2, _au2 = make_service(admin=admin2)
    with pytest.raises(TenantDbError) as ei:
        await service2.ensure_company_database(COMPANY)
    assert ei.value.code == PRIVILEGE_DENIED
    assert ei.value.http_status == 403


async def test_not_found_on_revoke_without_grant() -> None:
    service, _admin, _secrets, _audit = make_service()
    with pytest.raises(TenantDbError) as ei:
        await service.revoke_plugin(COMPANY, PLUGIN)
    assert ei.value.code == NOT_FOUND


async def test_not_found_on_uninstall_without_grant() -> None:
    service, _admin, _secrets, _audit = make_service()
    with pytest.raises(TenantDbError) as ei:
        await service.uninstall_plugin(COMPANY, PLUGIN)
    assert ei.value.code == NOT_FOUND


async def test_not_found_on_drop_missing_company_database() -> None:
    service, _admin, _secrets, _audit = make_service()
    with pytest.raises(TenantDbError) as ei:
        await service.drop_company_database(COMPANY)
    assert ei.value.code == NOT_FOUND
# endregion


# region revoke_plugin
async def test_revoke_keeps_database_and_schema() -> None:
    """§5.5: revoke отзывает доступ, БД/схема СОХРАНЯЮТСЯ."""
    service, admin, secrets, audit = make_service()
    await service.grant_plugin(COMPANY, PLUGIN)
    await service.revoke_plugin(COMPANY, PLUGIN)

    assert DB in admin.databases
    assert SCHEMA in admin.schemas[DB]
    assert ROLE in admin.roles
    assert admin.count("DROP SCHEMA") == 0
    assert admin.count("DROP ROLE") == 0
    assert admin.count("DROP DATABASE") == 0
    assert any("NOLOGIN" in t for t in admin.texts(prefix="ALTER ROLE"))
    assert admin.count("REVOKE CONNECT") == 1
    # Старый DSN инвалидирован, plaintext не остался.
    assert all(v == "" for v in secrets.values.values())
    assert "kept" in audit.all_payload_text()
# endregion


# region uninstall_plugin — §11.15 «не трогает чужие БД»
async def test_uninstall_drops_only_own_schema_and_role() -> None:
    service, admin, _secrets, audit = make_service()
    # Чужая компания и чужой плагин в той же компании.
    await service.grant_plugin("co_999", "mod_other")
    await service.grant_plugin(COMPANY, "mod_other")
    await service.grant_plugin(COMPANY, PLUGIN)

    other_db = tenant_db_name("co_999")
    other_role = plugin_role_name("co_999", "mod_other")
    other_schema = plugin_schema_name("mod_other")
    same_company_other_role = plugin_role_name(COMPANY, "mod_other")
    same_company_other_schema = plugin_schema_name("mod_other")

    await service.uninstall_plugin(COMPANY, PLUGIN)

    # Удалены ровно наши объекты.
    assert ROLE not in admin.roles
    assert SCHEMA not in admin.schemas[DB]
    assert f'DROP ROLE IF EXISTS "{ROLE}"' in admin.texts(prefix="DROP ROLE")
    # Ни одного DROP для чужих имён.
    all_drops = " ".join(admin.texts(prefix="DROP"))
    assert other_role not in all_drops
    assert same_company_other_role not in all_drops
    assert other_db not in all_drops
    assert "prodavan" not in all_drops
    # Чужие БД/схемы/роли живы.
    assert other_db in admin.databases
    assert other_role in admin.roles
    assert other_schema in admin.schemas[other_db]
    assert same_company_other_role in admin.roles
    assert same_company_other_schema in admin.schemas[DB]
    assert DB in admin.databases  # БД компании сохраняется при uninstall
    # Явная пометка в аудите.
    uninstall_events = [e for e in audit.events if e["event"] == "tenantdb.plugin.uninstall"]
    assert len(uninstall_events) == 1
    assert uninstall_events[0]["payload"]["other_databases_touched"] is False
    assert uninstall_events[0]["payload"]["snapshot_required"] is False


async def test_uninstall_order_revoke_before_drop() -> None:
    """DROP ROLE только после REVOKE ALL ON SCHEMA + DROP SCHEMA (иначе 2BP01)."""
    service, admin, _secrets, _audit = make_service()
    await service.grant_plugin(COMPANY, PLUGIN)
    await service.uninstall_plugin(COMPANY, PLUGIN)
    texts = admin.texts()
    idx_revoke = next(i for i, t in enumerate(texts) if t.startswith("REVOKE ALL ON SCHEMA"))
    idx_drop_schema = next(i for i, t in enumerate(texts) if t.startswith("DROP SCHEMA"))
    idx_drop_role = next(i for i, t in enumerate(texts) if t.startswith("DROP ROLE"))
    assert idx_revoke < idx_drop_schema < idx_drop_role
    # DROP SCHEMA шёл в target БД компании, DROP ROLE — в cluster.
    drop_schema_stmt = next(s for s in admin.statements if s[0].startswith("DROP SCHEMA"))
    assert drop_schema_stmt[2] == AdminTarget(scope="database", database=DB)
    drop_role_stmt = next(s for s in admin.statements if s[0].startswith("DROP ROLE"))
    assert drop_role_stmt[2] == AdminTarget(scope="cluster")


async def test_uninstall_no_snapshot_required() -> None:
    """Данные dev эфемерны — snapshot/бэкап НЕ требуется (§5.5, §13.1)."""
    service, admin, _secrets, _audit = make_service()
    await service.grant_plugin(COMPANY, PLUGIN)
    await service.uninstall_plugin(COMPANY, PLUGIN)
    joined = " ".join(admin.texts()).lower()
    assert "snapshot" not in joined
    assert "backup" not in joined
    assert "pg_dump" not in joined
# endregion


# region drop_company_database
async def test_drop_company_database_terminates_and_drops_only_own_db() -> None:
    service, admin, _secrets, _audit = make_service()
    await service.grant_plugin("co_999", PLUGIN)
    await service.grant_plugin(COMPANY, PLUGIN)
    other_db = tenant_db_name("co_999")

    await service.drop_company_database(COMPANY)

    assert DB not in admin.databases
    assert other_db in admin.databases
    # ``pg_terminate_backend`` стоит в середине SELECT, не в начале:
    # проверяем подстроку, а не count(prefix=...).
    assert any("pg_terminate_backend" in t for t in admin.texts())
    assert sum("pg_terminate_backend" in t for t in admin.texts()) == 1
    drop_texts = admin.texts(prefix="DROP DATABASE")
    assert len(drop_texts) == 1
    assert other_db not in drop_texts[0]
    assert "prodavan" not in drop_texts[0]


async def test_drop_company_database_validates_id() -> None:
    service, admin, _secrets, _audit = make_service()
    with pytest.raises(TenantDbError) as ei:
        await service.drop_company_database("co; DROP DATABASE prodavan")
    assert ei.value.code == INVALID_IDENTIFIER
    assert admin.statements == []
# endregion


# region Протоколы и контракты
def test_error_codes_contract_is_complete() -> None:
    """Все пять кодов из задания F3 существуют и уникальны."""
    assert ALL_CODES == frozenset(
        {INVALID_IDENTIFIER, ADMIN_UNAVAILABLE, ALREADY_EXISTS, NOT_FOUND, PRIVILEGE_DENIED}
    )
    # Каждая фабрика даёт свой код и безопасный http_status.
    assert TenantDbError.invalid_identifier("x", kind="company_id").code == INVALID_IDENTIFIER
    assert TenantDbError.admin_unavailable("x").code == ADMIN_UNAVAILABLE
    assert TenantDbError.already_exists("x", object_kind="db", safe_name="tenant_co").code == ALREADY_EXISTS
    assert TenantDbError.not_found("x", object_kind="db", safe_name="tenant_co").code == NOT_FOUND
    assert TenantDbError.privilege_denied("x", safe_name="tenant_co").code == PRIVILEGE_DENIED
    with pytest.raises(ValueError):
        TenantDbError(code="UNKNOWN", operation="x", detail="x")


def test_ports_are_runtime_checkable_protocols() -> None:
    """Порты — Protocol; fakes структурно им соответствуют."""
    from prodavan.application.plugins.tenantdb.service import AdminConnectionPort, AuditPort, SecretPort

    assert isinstance(FakeAdminConnection(), AdminConnectionPort)
    assert isinstance(FakeSecretStore(), SecretPort)
    assert isinstance(FakeAudit(), AuditPort)


def test_execute_result_first_value() -> None:
    assert ExecuteResult(rows=((1,),)).first_value == 1
    assert ExecuteResult(rows=()).first_value is None
    assert ExecuteResult(rows=({"x": 7},)).first_value == 7
    assert ExecuteResult(rows=([9, 8],)).first_value == 9


def test_admin_target_validation() -> None:
    with pytest.raises(ValueError):
        AdminTarget(scope="database")
    with pytest.raises(ValueError):
        AdminTarget(scope="cluster", database=DB)


async def test_audit_never_contains_raw_sql_or_params() -> None:
    """§11.11: аудит не содержит raw SQL, параметров, токенов, payload."""
    service, _admin, _secrets, audit = make_service()
    await service.grant_plugin(COMPANY, PLUGIN)
    for event in audit.events:
        payload = event["payload"]
        lowered = {k.lower(): v for k, v in payload.items()}
        assert "sql" not in lowered
        assert "text" not in lowered
        assert "params" not in lowered
        assert "password" not in lowered
        assert "dsn" not in lowered
        assert "token" not in lowered
        # Значения — только нормализованные имена/флаги/secret_ref.
        assert "SELECT" not in str(payload)
        assert "CREATE" not in str(payload)
