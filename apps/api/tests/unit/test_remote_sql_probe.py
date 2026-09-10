"""Unit tests for remote SQL probe helpers."""

from __future__ import annotations

import pytest

from prodavan.application.content.remote_sql_probe import (
    parse_remote_table,
    postgres_dsn_database_name,
    postgres_dsn_table_query,
    resolve_connect_dsn,
    resolve_remote_catalog_target,
    sanitize_env_row_id,
    strip_postgres_driver_query,
    validate_postgres_dsn,
)
from prodavan.application.pod_service.container_env_resolver import (
    build_foreach_dsn_env_name,
    foreach_rows_match,
)
from prodavan.domain.errors import AppError


def test_validate_postgres_dsn_ok() -> None:
    dsn = validate_postgres_dsn("postgresql://u:p@db.example:5432/prices")
    assert dsn.startswith("postgresql://")


def test_validate_postgres_dsn_rejects_mysql() -> None:
    with pytest.raises(AppError):
        validate_postgres_dsn("mysql://u:p@h/db")


def test_parse_remote_table_schema_optional() -> None:
    assert parse_remote_table("items") == ("public", "items")
    assert parse_remote_table("sales.prices") == ("sales", "prices")


def test_parse_remote_table_rejects_injection() -> None:
    with pytest.raises(AppError):
        parse_remote_table("prices; drop table x")
    with pytest.raises(AppError):
        parse_remote_table("a.b.c")


def test_sanitize_env_row_id() -> None:
    assert sanitize_env_row_id("row-abc_01") == "ROW_ABC_01"
    assert sanitize_env_row_id("12x").startswith("R_")


def test_build_foreach_dsn_env_name() -> None:
    name = build_foreach_dsn_env_name("EQUIPMENT_CATALOG_DSN_", "cab_row_1")
    assert name == "EQUIPMENT_CATALOG_DSN_CAB_ROW_1"


def test_foreach_rows_match() -> None:
    body = {"source_kind": "remote", "status": "ready", "paused": False}
    assert foreach_rows_match(body, {"source_kind": "remote", "status": "ready"})
    assert not foreach_rows_match(body, {"source_kind": "local"})
    assert not foreach_rows_match({**body, "paused": True}, {"paused": False})


def test_strip_postgres_driver_query_removes_table() -> None:
    raw = "postgresql://s4b:s4b@h:5433/s4b_catalog?table=public.offers&sslmode=prefer"
    stripped = strip_postgres_driver_query(raw)
    assert "table=" not in stripped
    assert "sslmode=prefer" in stripped
    assert postgres_dsn_table_query(raw) == "public.offers"


def test_resolve_connect_dsn_strips_table_query() -> None:
    dsn = resolve_connect_dsn(
        dsn="postgresql://s4b:s4b@h:5433/s4b_catalog?table=public.supplier_price_items"
    )
    assert "table=" not in dsn
    assert "/s4b_catalog" in dsn


def test_resolve_remote_requires_table() -> None:
    with pytest.raises(AppError, match="remote_table is required"):
        resolve_remote_catalog_target(
            dsn="postgresql://s4b:s4b@h:5433/s4b_catalog",
            remote_table_field="",
        )


def test_resolve_remote_from_query_table() -> None:
    connect, schema, table = resolve_remote_catalog_target(
        dsn="postgresql://s4b:s4b@h:5433/s4b_catalog?table=public.supplier_price_items",
        remote_table_field="",
    )
    assert "table=" not in connect
    assert (schema, table) == ("public", "supplier_price_items")


def test_resolve_remote_from_field() -> None:
    connect, schema, table = resolve_remote_catalog_target(
        dsn="postgresql://s4b:s4b@h:5433/s4b_catalog",
        remote_table_field="sales.prices",
    )
    assert (schema, table) == ("sales", "prices")
    assert postgres_dsn_database_name(connect) == "s4b_catalog"


def test_resolve_connect_from_database_field() -> None:
    dsn = resolve_connect_dsn(
        dsn="postgresql://s4b:s4b@h:5433",
        remote_database_field="s4b_catalog",
    )
    assert dsn.endswith("/s4b_catalog")


def test_normalize_moves_schema_table_out_of_database_field() -> None:
    from prodavan.application.content.remote_sql_probe import normalize_remote_db_and_table

    db, table = normalize_remote_db_and_table(
        dsn="postgresql://s4b:s4b@h:5433",
        remote_database_field="public.supplier_price_items",
        remote_table_field="",
    )
    assert db == ""
    assert table == "public.supplier_price_items"


def test_normalize_path_schema_table_needs_database_field() -> None:
    from prodavan.application.content.remote_sql_probe import normalize_remote_db_and_table

    db, table = normalize_remote_db_and_table(
        dsn="postgresql://s4b:s4b@h:5433/public.offers",
        remote_database_field="s4b_catalog",
        remote_table_field="",
    )
    assert db == "s4b_catalog"
    assert table == "public.offers"
    connect = resolve_connect_dsn(
        dsn="postgresql://s4b:s4b@h:5433/public.offers",
        remote_database_field="s4b_catalog",
    )
    assert connect.endswith("/s4b_catalog")
    assert "public.offers" not in connect


def test_resolve_connect_rejects_schema_table_as_database() -> None:
    with pytest.raises(AppError, match="not schema.table"):
        resolve_connect_dsn(
            dsn="postgresql://s4b:s4b@h:5433",
            remote_database_field="public.supplier_price_items",
        )


def test_apply_overrides_user_password_and_db() -> None:
    from prodavan.application.content.remote_sql_probe import apply_remote_connect_overrides

    dsn = apply_remote_connect_overrides(
        "postgresql://172.21.176.1:5433",
        database="s4b_catalog",
        user="s4b",
        password="secret",
    )
    assert dsn.startswith("postgresql://s4b:secret@172.21.176.1:5433/s4b_catalog")


def test_list_databases_uses_maintenance_db(monkeypatch: pytest.MonkeyPatch) -> None:
    from prodavan.application.content.remote_sql_probe import (
        apply_remote_connect_overrides,
        resolve_connect_dsn,
    )

    dsn = resolve_connect_dsn(
        dsn="postgresql://s4b:s4b@h:5433",
        for_database_list=True,
        require_database=False,
    )
    assert dsn.endswith("/postgres")
    assert "s4b:s4b@" in dsn

    overlaid = apply_remote_connect_overrides(
        "postgresql://h:5433",
        user="u",
        password="p",
        for_database_list=True,
    )
    assert overlaid.endswith("/postgres")
    assert "u:p@" in overlaid


def test_trailing_slash_dsn_has_no_database() -> None:
    assert postgres_dsn_database_name("postgresql://s4b:s4b@h:5433/") is None
    assert postgres_dsn_database_name("postgresql://s4b:s4b@h:5433") is None


def test_is_remote_auth_failure() -> None:
    from prodavan.application.content.remote_sql_probe import is_remote_auth_failure

    assert is_remote_auth_failure(Exception("password authentication failed for user"))
    assert is_remote_auth_failure(Exception("error 28P01"))
    assert not is_remote_auth_failure(Exception("connection refused"))


def test_product_seed_action_fallback() -> None:
    from prodavan.application.modules.module_action_executor import _product_seed_action

    action = _product_seed_action(
        module_id="mod_equipment",
        action_id="list_catalog_remote_databases",
    )
    assert action is not None
    assert action["kind"] == "content.list_remote_sql_databases"
    tables = _product_seed_action(
        module_id="mod_equipment",
        action_id="list_catalog_remote_tables",
    )
    assert tables is not None
    assert tables["kind"] == "content.list_remote_sql_tables"


@pytest.mark.asyncio
async def test_list_remote_databases(monkeypatch: pytest.MonkeyPatch) -> None:
    from prodavan.application.content.remote_sql_probe import list_remote_databases

    class _FakeConn:
        async def fetch(self, *_a, **_k):
            return [{"datname": "postgres"}, {"datname": "s4b_catalog"}]

        async def close(self):
            return None

    async def _connect(**kwargs):
        assert str(kwargs.get("dsn") or "").endswith("/postgres")
        return _FakeConn()

    monkeypatch.setattr(
        "prodavan.application.content.remote_sql_probe.asyncpg.connect",
        _connect,
    )
    dbs = await list_remote_databases(dsn="postgresql://s4b:s4b@h:5433")
    assert [d.name for d in dbs] == ["postgres", "s4b_catalog"]


@pytest.mark.asyncio
async def test_probe_remote_postgres_columns_and_count(monkeypatch: pytest.MonkeyPatch) -> None:
    from prodavan.application.content.remote_sql_probe import (
        RemoteSqlProbeResult,
        probe_remote_postgres,
    )

    class _FakeConn:
        async def fetch(self, *_a, **_k):
            return [{"column_name": "sku"}, {"column_name": "price"}]

        async def fetchval(self, *_a, **_k):
            return 42

        async def close(self):
            return None

    async def _connect(**kwargs):
        assert "table=" not in str(kwargs.get("dsn") or "")
        return _FakeConn()

    monkeypatch.setattr(
        "prodavan.application.content.remote_sql_probe.asyncpg.connect",
        _connect,
    )
    result = await probe_remote_postgres(
        dsn="postgresql://u:p@localhost:5432/db?table=public.prices",
        remote_table="",
    )
    assert isinstance(result, RemoteSqlProbeResult)
    assert result.columns == ["sku", "price"]
    assert result.row_count == 42
    assert result.schema == "public"
    assert result.table == "prices"
