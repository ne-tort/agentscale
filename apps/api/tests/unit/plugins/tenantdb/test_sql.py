"""Тесты генератора DDL (§2.2, §3.3, §5.5, §11 критерии 1/5/15).

Ключевые кейсы задания F3:
  * role DDL содержит ВСЕ NOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOBYPASSRLS/
    NOREPLICATION/CONNECTION LIMIT 10;
  * ``search_path`` без версии плагина;
  * РБД/RLS/GUC ``app.tenant_id`` не применяются;
  * ``pgaudit`` не упоминается как замена привилегий;
  * uninstall дропает только свою схему/роль;
  * CREATE DATABASE идемпотентен через SELECT 1 ...;
  * DDL не содержит непроверенной конкатенации, пароль не утекает в repr.
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.tenantdb import INVALID_IDENTIFIER, TenantDbError
from prodavan.application.plugins.tenantdb import sql
from prodavan.application.plugins.tenantdb.naming import (
    plugin_role_name,
    plugin_schema_name,
    plugin_search_path,
    tenant_db_name,
)

DB = tenant_db_name("co_123")
ROLE = plugin_role_name("co_123", "mod_equipment")
SCHEMA = plugin_schema_name("mod_equipment")
PATH = plugin_search_path("mod_equipment")


# region CREATE ROLE — полный набор ограничений (§11 критерий 1)
def test_create_role_contains_all_privilege_flags() -> None:
    """Обязательный набор флагов из §2.2 — НИ ОДИН не потерян."""
    stmt = sql.create_role(ROLE, password="s3cr3t'with'quote")
    text = stmt.text
    assert text.startswith('CREATE ROLE "tenant_co_123_plugin_mod_equipment"')
    assert "LOGIN" in text
    assert "PASSWORD" in text
    for flag in (
        "NOSUPERUSER",
        "NOCREATEDB",
        "NOCREATEROLE",
        "NOREPLICATION",
        "NOBYPASSRLS",
    ):
        assert flag in text, f"missing {flag}"
    assert "CONNECTION LIMIT 10" in text
    # Никаких CREATEDB/CREATEROLE/SUPERUSER/BYPASSRLS без NO-.
    assert " CREATEDB" not in text
    assert " CREATEROLE" not in text
    assert " SUPERUSER" not in text
    assert " BYPASSRLS" not in text


def test_password_quote_escaping() -> None:
    """Одинарные кавычки пароля удваиваются, инъекция через пароль не проходит."""
    stmt = sql.create_role(ROLE, password="x'; DROP DATABASE prodavan; --")
    assert "''; DROP DATABASE prodavan; --" in stmt.text
    # Пароль остаётся ОДНИМ строковым литералом: после закрывающей кавычки
    # сразу идут флаги, второго statement нет.
    assert stmt.text.count("PASSWORD") == 1
    assert ";" in stmt.text  # только внутри литерала
    assert stmt.text.rstrip().endswith("CONNECTION LIMIT 10")


def test_password_never_in_repr_and_safe_text() -> None:
    """DSN/пароль НЕ в логах: __repr__ и safe_text скрывают литерал."""
    secret = "SUPER_SECRET_PASSWORD_123"
    stmt = sql.create_role(ROLE, password=secret)
    assert secret not in repr(stmt)
    assert secret not in str(stmt)
    assert secret not in stmt.safe_text()
    assert secret not in stmt.safe_repr()
    assert "***" in stmt.safe_text()
    # params в repr не печатаются (там теоретически может быть что угодно).
    assert "<redacted>" in repr(stmt)


def test_alter_role_password_is_rotation_not_recreate() -> None:
    """Повторный grant ротирует пароль через ALTER, а НЕ через CREATE ROLE."""
    stmt = sql.alter_role_password(ROLE, password="new_pwd")
    assert stmt.text.startswith("ALTER ROLE")
    assert "PASSWORD" in stmt.text
    assert not stmt.text.startswith("CREATE ROLE")
    assert "new_pwd" not in stmt.safe_repr()
# endregion


# region search_path без версии (§3.3)
def test_search_path_ddl_has_no_version() -> None:
    stmt = sql.alter_role_search_path(ROLE, schema=PATH)
    assert f"SET search_path = '{SCHEMA}'" in stmt.text
    assert "v1" not in stmt.text
    assert "v2" not in stmt.text
    assert "1.2" not in stmt.text
    assert "public" not in stmt.text
    assert "," not in stmt.text  # ровно одна схема, без чужих
    assert stmt.text.count("'") == 2  # один строковый литерал


def test_timeouts_are_sane_defaults_not_rate_limit() -> None:
    """statement_timeout/lock_timeout — предохранители, не защита (§13.1)."""
    assert "statement_timeout = '30s'" in sql.alter_role_statement_timeout(ROLE).text
    assert "lock_timeout = '10s'" in sql.alter_role_lock_timeout(ROLE).text
# endregion


# region CREATE DATABASE / идемпотентность / админский порт
def test_database_exists_is_parameterized() -> None:
    """``SELECT 1 FROM pg_database WHERE datname=:name`` — образец userdb-init.yaml."""
    stmt = sql.database_exists(DB)
    assert stmt.text == "SELECT 1 FROM pg_database WHERE datname = :name"
    assert stmt.params == {"name": DB}
    assert "co_123" not in stmt.text  # значение НЕ в тексте SQL


def test_post_state_probes_use_exact_bind_values() -> None:
    login = sql.role_login_enabled(ROLE)
    assert login.text == "SELECT 1 FROM pg_roles WHERE rolname = :rolname AND rolcanlogin"
    assert login.params == {"rolname": ROLE}

    connect = sql.database_connect_granted(DB, ROLE)
    assert "aclexplode" in connect.text
    assert "JOIN pg_roles AS grantee" in connect.text
    assert "acl.privilege_type = 'CONNECT'" in connect.text
    assert connect.params == {"name": DB, "role": ROLE}
    assert DB not in connect.text and ROLE not in connect.text
    # PUBLIC is deliberately not treated as an explicit per-role GRANT.
    assert "grantee.oid = acl.grantee" in connect.text


def test_create_database_is_quoted_and_no_if_not_exists() -> None:
    stmt = sql.create_database(DB)
    assert stmt.text == 'CREATE DATABASE "tenant_co_123"'
    assert "IF NOT EXISTS" not in stmt.text  # у CREATE DATABASE его нет
    assert "prodavan" not in stmt.text


def test_create_database_with_admin_owner() -> None:
    stmt = sql.create_database(DB, owner="platform_admin")
    assert 'OWNER "platform_admin"' in stmt.text


def test_ddl_rejects_unvalidated_identifiers() -> None:
    """Defense in depth: ``sql.py`` не даст подставить инъекцию напрямую."""
    payload = 'co_123"; DROP DATABASE prodavan; --'
    for fn in (
        lambda: sql.database_exists(payload),
        lambda: sql.role_login_enabled(payload),
        lambda: sql.database_connect_granted(payload, ROLE),
        lambda: sql.database_connect_granted(DB, payload),
        lambda: sql.create_database(payload),
        lambda: sql.create_role(payload, password="x"),
        lambda: sql.drop_database(payload),
        lambda: sql.drop_role(payload),
        lambda: sql.drop_schema(payload),
        lambda: sql.grant_connect(payload, ROLE),
        lambda: sql.alter_role_search_path(payload, schema=PATH),
        lambda: sql.terminate_backend(payload),
    ):
        with pytest.raises(TenantDbError) as ei:
            fn()
        assert ei.value.code == INVALID_IDENTIFIER
# endregion


# region GRANT / CREATE SCHEMA
def test_grant_connect_ddl() -> None:
    stmt = sql.grant_connect(DB, ROLE)
    assert stmt.text == f'GRANT CONNECT ON DATABASE "{DB}" TO "{ROLE}"'


def test_create_schema_owner_is_plugin_role() -> None:
    """§2.2: owner схемы = роль плагина (не платформенная роль)."""
    stmt = sql.create_schema(SCHEMA, owner=ROLE)
    assert f'CREATE SCHEMA "{SCHEMA}" AUTHORIZATION "{ROLE}"' == stmt.text
# endregion


# region RLS / GUC / pgaudit — что ЗАПРЕЩЕНО (§2.4, §13.1)
ALL_DDL_FUNCTIONS = [
    lambda: sql.create_database(DB),
    lambda: sql.create_role(ROLE, password="x"),
    lambda: sql.grant_connect(DB, ROLE),
    lambda: sql.create_schema(SCHEMA, owner=ROLE),
    lambda: sql.alter_role_search_path(ROLE, schema=PATH),
    lambda: sql.alter_role_statement_timeout(ROLE),
    lambda: sql.alter_role_lock_timeout(ROLE),
    lambda: sql.alter_role_no_login(ROLE),
    lambda: sql.revoke_connect(DB, ROLE),
    lambda: sql.revoke_all_on_schema(SCHEMA, ROLE),
    lambda: sql.revoke_public_connect(DB),
    lambda: sql.drop_schema(SCHEMA),
    lambda: sql.drop_role(ROLE),
    lambda: sql.drop_database(DB),
    lambda: sql.terminate_backend(DB),
]


@pytest.mark.parametrize("make", ALL_DDL_FUNCTIONS)
def test_no_rls_and_no_tenant_guc_in_ddl(make: object) -> None:
    """RLS НЕ граница (§2.4), ``app.tenant_id`` GUC не применяется."""
    text = make().text.upper()  # type: ignore[operator]
    assert "ROW LEVEL SECURITY" not in text
    assert "CREATE POLICY" not in text
    assert "FORCE ROW" not in text
    assert "APP.TENANT_ID" not in text
    assert "SET APP." not in text


def test_module_docstring_documents_rls_and_guc_rejection() -> None:
    """Явная документация отказа от RLS/GUC — по требованию задания."""
    doc = sql.__doc__ or ""
    assert "RLS не используется как граница" in doc
    assert "app.tenant_id" in doc
    assert "не применяется" in doc


def test_pgaudit_is_observation_not_privilege_substitute() -> None:
    """§11.5: pgaudit — наблюдение, а НЕ замена привилегий.

    В DDL нет ни одного упоминания pgaudit; в докстринге оно есть ровно
    с формулировкой «НЕ замена привилегий».
    """
    for make in ALL_DDL_FUNCTIONS:
        assert "pgaudit" not in make().text.lower()  # type: ignore[operator]
    doc = (sql.__doc__ or "").lower()
    assert "pgaudit" in doc
    assert "не замена привилегий" in doc
    assert "не средство запрета" in doc
# endregion


# region REVOKE / DROP / PUBLIC
def test_revoke_no_login_is_instant_revocation() -> None:
    """revoke = NOLOGIN (мгновенно), БД/схема сохраняются."""
    stmt = sql.alter_role_no_login(ROLE)
    assert "NOLOGIN" in stmt.text
    assert "DROP" not in stmt.text


def test_revoke_connect_present() -> None:
    assert f'REVOKE CONNECT ON DATABASE "{DB}" FROM "{ROLE}"' == sql.revoke_connect(DB, ROLE).text


def test_drop_schema_only_targets_own_schema() -> None:
    """§11.15: uninstall не трогает чужую БД/схему."""
    stmt = sql.drop_schema(SCHEMA)
    assert f'DROP SCHEMA IF EXISTS "{SCHEMA}"' in stmt.text
    assert "CASCADE" in stmt.text
    assert "tenant_co_999" not in stmt.text
    assert "prodavan" not in stmt.text
    assert "module_other" not in stmt.text


def test_drop_role_and_database_are_scoped() -> None:
    assert f'DROP ROLE IF EXISTS "{ROLE}"' == sql.drop_role(ROLE).text
    assert f'DROP DATABASE IF EXISTS "{DB}"' == sql.drop_database(DB).text
    assert "prodavan" not in sql.drop_database(DB).text


def test_revoke_public_connect_is_bootstrap_only_helper() -> None:
    """Заготовка для задачи 14: снимает PUBLIC, НЕ вызывается сервисом."""
    stmt = sql.revoke_public_connect(DB)
    assert "REVOKE CONNECT, TEMPORARY ON DATABASE" in stmt.text
    assert "FROM PUBLIC" in stmt.text
    doc = sql.revoke_public_connect.__doc__ or ""
    assert "НЕ вызывать из сервиса" in doc
    assert "админской" in doc  # предупреждение про потерю доступа


def test_grant_connect_admin_companion_exists() -> None:
    """Компаньон: выдать CONNECT админской роли ДО revoke PUBLIC."""
    stmt = sql.grant_connect_admin(DB, "platform_admin")
    assert 'TO "platform_admin"' in stmt.text


def test_terminate_backend_is_parameterized() -> None:
    stmt = sql.terminate_backend(DB)
    assert ":name" in stmt.text
    assert stmt.params == {"name": DB}
    assert DB not in stmt.text
# endregion


# region Общий контракт DdlStatement
def test_all_ddl_statements_are_dataclass_with_params_dict() -> None:
    for make in ALL_DDL_FUNCTIONS:
        stmt = make()  # type: ignore[operator]
        assert isinstance(stmt, sql.DdlStatement)
        assert isinstance(stmt.params, dict)
        assert isinstance(stmt.text, str) and stmt.text


def test_as_execute_args_returns_copy() -> None:
    stmt = sql.database_exists(DB)
    text, params = sql.as_execute_args(stmt)
    assert text == stmt.text
    assert params == stmt.params
    assert params is not stmt.params  # копия, порт не mutate исходник


def test_schema_exists_uses_parameter_and_database_scope_hint() -> None:
    stmt = sql.schema_exists(SCHEMA)
    assert ":schema" in stmt.text
    assert stmt.params == {"schema": SCHEMA}
    doc = sql.schema_exists.__doc__ or ""
    assert "БД компании" in doc
# endregion
