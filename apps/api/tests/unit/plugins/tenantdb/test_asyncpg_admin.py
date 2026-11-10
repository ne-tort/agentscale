"""Unit tests for asyncpg tenant admin; no socket or live PostgreSQL is used.

Mandatory follow-up after the verification freeze: run the same grant/replay/
revoke/uninstall/drop sequence against a disposable PostgreSQL with verify-full
TLS, then confirm pg_database/pg_roles/pg_namespace and the tenant DSN login.
"""

from __future__ import annotations

import ssl
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

from prodavan.application.plugins.tenantdb.errors import (
    ADMIN_UNAVAILABLE,
    INVALID_IDENTIFIER,
    PRIVILEGE_DENIED,
    TenantDbError,
)
from prodavan.application.plugins.tenantdb.naming import (
    plugin_role_name,
    plugin_schema_name,
    tenant_db_name,
)
from prodavan.application.plugins.tenantdb.service import (
    AdminTarget,
    ExecuteResult,
    TenantDatabaseService,
)
from prodavan.infrastructure.plugins.tenantdb.asyncpg_admin import (
    AsyncpgAdminConfig,
    AsyncpgAdminConnection,
    TenantAdminConfigError,
    TenantAdminExecutionError,
    _compile_named_parameters,
)
from tests.unit.plugins.tenantdb.conftest import (
    FakeAdminConnection,
    FakeAudit,
    FakeSecretStore,
)

DSN = "postgresql://tenant_admin:dsn-password-marker@db.internal:5544/postgres"
COMPANY = "co_123"
PLUGIN = "mod_equipment"
DB = tenant_db_name(COMPANY)
ROLE = plugin_role_name(COMPANY, PLUGIN)
SCHEMA = plugin_schema_name(PLUGIN)


@dataclass
class FakeAsyncpgDriver:
    state: FakeAdminConnection = field(default_factory=FakeAdminConnection)
    pools: list["FakeAsyncpgPool"] = field(default_factory=list)
    fail_next: Exception | None = None
    suppress_ddl: bool = False

    async def create_pool(self, **kwargs: Any) -> "FakeAsyncpgPool":
        pool = FakeAsyncpgPool(self, kwargs)
        self.pools.append(pool)
        return pool


class FakeAsyncpgPool:
    def __init__(self, driver: FakeAsyncpgDriver, kwargs: dict[str, Any]) -> None:
        self.driver = driver
        self.kwargs = kwargs
        self.connection = FakeAsyncpgConnection(self)
        self.close_calls = 0

    @asynccontextmanager
    async def acquire(self, *, timeout: float):
        if self.close_calls:
            raise RuntimeError("pool is closed")
        yield self.connection

    async def execute(self, query: str, *args: Any, timeout: float) -> str:
        # Pool.execute is used only for the startup SELECT 1 ping.
        if query != "SELECT 1" or args:
            raise AssertionError("unexpected pool.execute query")
        return "SELECT 1"

    async def close(self) -> None:
        self.close_calls += 1


class FakeAsyncpgConnection:
    def __init__(self, pool: FakeAsyncpgPool) -> None:
        self.pool = pool
        self.calls: list[tuple[str, str, tuple[Any, ...], float]] = []

    async def fetch(self, query: str, *args: Any, timeout: float) -> list[tuple[Any, ...]]:
        self.calls.append(("fetch", query, args, timeout))
        result = await self._invoke(query, args)
        return [tuple(row) for row in result.rows]

    async def execute(self, query: str, *args: Any, timeout: float) -> str:
        self.calls.append(("execute", query, args, timeout))
        await self._invoke(query, args)
        return "OK"

    async def _invoke(self, query: str, args: tuple[Any, ...]) -> ExecuteResult:
        driver = self.pool.driver
        if driver.fail_next is not None:
            error = driver.fail_next
            driver.fail_next = None
            raise error
        if query == "SELECT 1" and not args:
            return ExecuteResult(rows=((1,),))
        if driver.suppress_ddl and query.split(maxsplit=1)[0].upper() in {
            "CREATE",
            "DROP",
            "ALTER",
            "GRANT",
            "REVOKE",
        }:
            return ExecuteResult()
        named_query, params = _restore_named_binds(query, args)
        database = self.pool.kwargs["database"]
        target = (
            AdminTarget(scope="cluster")
            if database == "postgres"
            else AdminTarget(scope="database", database=database)
        )
        return await driver.state.execute(named_query, params, target=target)


def _restore_named_binds(query: str, args: tuple[Any, ...]) -> tuple[str, dict[str, Any]]:
    """Test-only inverse for the fixed service statements, not production SQL."""
    if "acl.privilege_type" in query:
        names = ("name", "role")
    elif "usename = $1" in query:
        names = ("role",)
    elif "rolname = $1" in query:
        names = ("rolname",)
    elif "schema_name = $1" in query:
        names = ("schema",)
    elif "datname = $1" in query:
        names = ("name",)
    elif args:
        raise AssertionError("unexpected asyncpg bind statement")
    else:
        names = ()
    assert len(names) == len(args)
    params = dict(zip(names, args, strict=True))
    restored = query
    for index, name in enumerate(names, start=1):
        restored = restored.replace("$" + str(index), ":" + name)
    return restored, params


def _config(**overrides: Any) -> AsyncpgAdminConfig:
    values: dict[str, Any] = {
        "connect_timeout_seconds": 2.0,
        "command_timeout_seconds": 4.0,
        "close_timeout_seconds": 1.0,
        "pool_min_size": 1,
        "pool_max_size": 3,
        "pool_max_inactive_lifetime_seconds": 45.0,
    }
    values.update(overrides)
    return AsyncpgAdminConfig.from_dsn(DSN, **values)


def _service(adapter: AsyncpgAdminConnection) -> tuple[TenantDatabaseService, FakeSecretStore, FakeAudit]:
    secrets = FakeSecretStore()
    audit = FakeAudit()
    return (
        TenantDatabaseService(
            admin=adapter,
            secrets=secrets,
            audit=audit,
            dsn_host="tenant-postgres.internal",
            dsn_port=5432,
            dsn_sslmode="require",
        ),
        secrets,
        audit,
    )


def test_dsn_parse_percent_decoding_and_tls_defaults() -> None:
    config = AsyncpgAdminConfig.from_dsn(
        "postgresql://tenant%5Fadmin:p%40ss%2Fword@db.internal:5544/admin%5Fdb"
    )
    assert config.host == "db.internal"
    assert config.port == 5544
    assert config.user == "tenant_admin"
    assert config.password == "p@ss/word"
    assert config.database == "admin_db"
    assert config.tls_mode == "verify-full"
    assert "p@ss/word" not in repr(config)
    assert "tenant_admin" not in repr(config)

    context = config.create_ssl_context()
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2

    verify_ca = AsyncpgAdminConfig.from_dsn(DSN + "?sslmode=verify-ca")
    ca_context = verify_ca.create_ssl_context()
    assert ca_context.check_hostname is False
    assert ca_context.verify_mode == ssl.CERT_REQUIRED

    required = AsyncpgAdminConfig.from_dsn(DSN + "?sslmode=require")
    required_context = required.create_ssl_context()
    assert required_context.check_hostname is False
    assert required_context.verify_mode == ssl.CERT_NONE


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://admin:secret@db/postgres?sslmode=disable",
        "postgresql://admin:secret@db/postgres?sslmode=prefer",
        "postgresql://admin@db/postgres",
        "sqlite://admin:secret@db/postgres",
        "postgresql://admin:secret@db/prodavan",
        "postgresql://admin:secret@db/postgres?application_name=raw",
    ],
)
def test_invalid_dsn_is_typed_and_redacted(dsn: str) -> None:
    with pytest.raises(TenantAdminConfigError) as caught:
        AsyncpgAdminConfig.from_dsn(dsn)
    assert "secret" not in str(caught.value)
    assert dsn not in str(caught.value)


def test_missing_tls_credentials_are_typed_and_redacted() -> None:
    with pytest.raises(TenantAdminConfigError) as pair_error:
        AsyncpgAdminConfig.from_dsn(DSN, tls_cert_file="client.crt")
    assert "dsn-password-marker" not in str(pair_error.value)

    config = AsyncpgAdminConfig.from_dsn(DSN, tls_ca_file="missing-ca.pem")
    with pytest.raises(TenantAdminConfigError) as load_error:
        config.create_ssl_context()
    assert "missing-ca.pem" not in str(load_error.value)
    assert "dsn-password-marker" not in str(load_error.value)


def test_named_binds_ignore_literals_comments_and_reuse_positions() -> None:
    query, args = _compile_named_parameters(
        "SELECT ':name', \"double:name\", :name, :name, $$:name$$, :role "
        "/* :ignored */ -- :also_ignored\n",
        {"name": DB, "role": ROLE},
    )
    assert "':name'" in query
    assert '"double:name"' in query
    assert "$$:name$$" in query
    assert "/* :ignored */" in query
    assert "-- :also_ignored" in query
    assert query.count("$1") == 2
    assert query.count("$2") == 1
    assert args == (DB, ROLE)

    with pytest.raises(TenantAdminExecutionError) as missing:
        _compile_named_parameters("SELECT :name", {})
    assert DB not in str(missing.value)
    with pytest.raises(TenantAdminExecutionError):
        _compile_named_parameters("SELECT 1", {"name": DB})


async def test_pool_timeouts_limits_health_and_idempotent_close() -> None:
    driver = FakeAsyncpgDriver()
    adapter = AsyncpgAdminConnection(_config(), pool_factory=driver.create_pool)
    assert await adapter.health() is False

    await adapter.open()
    await adapter.open()
    assert len(driver.pools) == 1
    kwargs = driver.pools[0].kwargs
    assert kwargs["host"] == "db.internal"
    assert kwargs["port"] == 5544
    assert kwargs["database"] == "postgres"
    assert kwargs["timeout"] == 2.0
    assert kwargs["command_timeout"] == 4.0
    assert kwargs["min_size"] == 1
    assert kwargs["max_size"] == 3
    assert kwargs["max_inactive_connection_lifetime"] == 45.0
    assert kwargs["ssl"].check_hostname is True
    assert await adapter.health() is True

    await adapter.close()
    await adapter.close()
    assert driver.pools[0].close_calls == 1
    assert await adapter.health() is False


async def test_service_exact_binds_replay_revoke_drop_and_close() -> None:
    driver = FakeAsyncpgDriver()
    adapter = AsyncpgAdminConnection(_config(), pool_factory=driver.create_pool)
    await adapter.open()
    service, secrets, audit = _service(adapter)

    first = await service.ensure_plugin(COMPANY, PLUGIN)
    replay = await service.ensure_plugin(COMPANY, PLUGIN)
    assert replay.secret_ref == first.secret_ref
    assert replay.created is False
    assert replay.rotated is False
    assert driver.state.databases == {DB}
    assert driver.state.roles == {ROLE}
    assert driver.state.schemas[DB] == {SCHEMA}
    assert driver.state.connect_grants == {(DB, ROLE)}
    assert driver.state.count("CREATE DATABASE") == 1
    assert driver.state.count("CREATE ROLE") == 1
    assert driver.state.count("CREATE SCHEMA") == 1
    assert secrets.rotates == []
    # One cluster pool and one tenant pool are reused by the replay.
    assert len(driver.pools) == 2

    tenant_pool = next(pool for pool in driver.pools if pool.kwargs["database"] == DB)
    fetch_calls = [call for call in tenant_pool.connection.calls if call[0] == "fetch"]
    schema_call = next(call for call in fetch_calls if "schema_name" in call[1])
    assert schema_call[1].endswith("schema_name = $1")
    assert schema_call[2] == (SCHEMA,)
    cluster = driver.pools[0].connection
    database_call = next(
        call for call in cluster.calls if call[0] == "fetch" and "db.datname" not in call[1]
        and "datname = $1" in call[1]
    )
    assert database_call[2] == (DB,)
    role_call = next(call for call in cluster.calls if "rolname = $1" in call[1])
    assert role_call[2] == (ROLE,)
    acl_call = next(call for call in cluster.calls if "acl.privilege_type" in call[1])
    assert "$1" in acl_call[1] and "$2" in acl_call[1]
    assert acl_call[2] == (DB, ROLE)

    ddl = " ".join(call[1] for call in cluster.calls if call[0] == "execute")
    assert f'CREATE DATABASE "{DB}"' in ddl
    assert f'CREATE ROLE "{ROLE}"' in ddl
    assert f'GRANT CONNECT ON DATABASE "{DB}" TO "{ROLE}"' in ddl
    tenant_ddl = " ".join(call[1] for call in tenant_pool.connection.calls if call[0] == "execute")
    assert f'CREATE SCHEMA "{SCHEMA}" AUTHORIZATION "{ROLE}"' in tenant_ddl

    assert "postgresql://" not in repr(first)
    assert "postgresql://" not in repr(replay)
    assert "postgresql://" not in audit.all_payload_text()
    assert "password" not in audit.all_payload_text().lower()

    await service.revoke_plugin(COMPANY, PLUGIN)
    assert driver.state.role_login[ROLE] is False
    assert (DB, ROLE) not in driver.state.connect_grants
    assert DB in driver.state.databases and SCHEMA in driver.state.schemas[DB]
    assert any(
        call[1] == f'REVOKE CONNECT ON DATABASE "{DB}" FROM "{ROLE}"'
        for call in cluster.calls
    )

    await service.rotate_plugin(COMPANY, PLUGIN)
    assert driver.state.role_login[ROLE] is True
    assert (DB, ROLE) in driver.state.connect_grants

    await service.uninstall_plugin(COMPANY, PLUGIN)
    assert ROLE not in driver.state.roles
    assert SCHEMA not in driver.state.schemas[DB]
    assert any(call[1] == f'DROP ROLE IF EXISTS "{ROLE}"' for call in cluster.calls)
    assert any(
        call[1] == f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'
        for call in tenant_pool.connection.calls
    )
    await service.drop_company_database(COMPANY)
    assert DB not in driver.state.databases
    assert any(call[1] == f'DROP DATABASE IF EXISTS "{DB}"' for call in cluster.calls)

    await adapter.close()
    assert all(pool.close_calls == 1 for pool in driver.pools)


async def test_post_sql_state_is_required_not_assumed() -> None:
    driver = FakeAsyncpgDriver(suppress_ddl=True)
    adapter = AsyncpgAdminConnection(_config(), pool_factory=driver.create_pool)
    await adapter.open()
    service, _secrets, _audit = _service(adapter)
    with pytest.raises(TenantDbError) as caught:
        await service.ensure_company_database(COMPANY)
    assert caught.value.code == ADMIN_UNAVAILABLE
    assert "database missing after create" in str(caught.value)
    await adapter.close()


async def test_driver_and_sqlstate_failures_are_sanitized() -> None:
    driver = FakeAsyncpgDriver()
    adapter = AsyncpgAdminConnection(_config(), pool_factory=driver.create_pool)
    await adapter.open()
    service, _secrets, _audit = _service(adapter)

    driver.fail_next = RuntimeError(
        "postgresql://tenant_admin:dsn-password-marker@db.internal/postgres "
        f"while binding {DB} and password=raw-role-password"
    )
    with pytest.raises(TenantDbError) as unavailable:
        await service.ensure_company_database(COMPANY)
    message = str(unavailable.value)
    assert unavailable.value.code == ADMIN_UNAVAILABLE
    for forbidden in ("dsn-password-marker", "raw-role-password", "db.internal", DB, "postgresql://"):
        assert forbidden not in message

    privilege = RuntimeError("42501 dsn-password-marker raw-role-password")
    privilege.sqlstate = "42501"
    driver.fail_next = privilege
    with pytest.raises(TenantDbError) as denied:
        await service.ensure_company_database(COMPANY)
    assert denied.value.code == PRIVILEGE_DENIED
    assert "dsn-password-marker" not in str(denied.value)
    assert "raw-role-password" not in str(denied.value)
    await adapter.close()


async def test_open_and_close_failures_are_redacted() -> None:
    driver = FakeAsyncpgDriver()
    adapter = AsyncpgAdminConnection(_config(), pool_factory=driver.create_pool)

    async def fail_open(**_kwargs: Any) -> Any:
        raise RuntimeError("open failed: dsn-password-marker at db.internal")

    adapter._pool_factory = fail_open
    with pytest.raises(TenantAdminExecutionError) as open_error:
        await adapter.open()
    assert "dsn-password-marker" not in str(open_error.value)
    assert "db.internal" not in str(open_error.value)

    adapter._pool_factory = driver.create_pool
    await adapter.open()

    async def fail_close() -> None:
        raise RuntimeError("close failed: dsn-password-marker")

    driver.pools[0].close = fail_close
    with pytest.raises(TenantAdminExecutionError) as close_error:
        await adapter.close()
    assert "dsn-password-marker" not in str(close_error.value)


async def test_database_target_cannot_escape_tenant_namespace() -> None:
    driver = FakeAsyncpgDriver()
    adapter = AsyncpgAdminConnection(_config(), pool_factory=driver.create_pool)
    await adapter.open()
    with pytest.raises(TenantDbError) as caught:
        await adapter.execute(
            "SELECT 1",
            target=AdminTarget(scope="database", database="prodavan"),
        )
    assert caught.value.code == INVALID_IDENTIFIER
    assert len(driver.pools) == 1
    await adapter.close()
