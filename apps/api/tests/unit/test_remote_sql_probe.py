"""Unit tests for remote SQL probe helpers."""

from __future__ import annotations

import pytest

from prodavan.application.content.remote_sql_probe import (
    parse_remote_table,
    sanitize_env_row_id,
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

    async def _connect(**_kwargs):
        return _FakeConn()

    monkeypatch.setattr(
        "prodavan.application.content.remote_sql_probe.asyncpg.connect",
        _connect,
    )
    result = await probe_remote_postgres(
        dsn="postgresql://u:p@localhost:5432/db",
        remote_table="public.prices",
    )
    assert isinstance(result, RemoteSqlProbeResult)
    assert result.columns == ["sku", "price"]
    assert result.row_count == 42
    assert result.schema == "public"
    assert result.table == "prices"
