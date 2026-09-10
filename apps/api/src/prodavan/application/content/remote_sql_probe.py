"""Live PostgreSQL probe for remote equipment catalogs (no data snapshot)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import asyncpg

from prodavan.domain.errors import AppError

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TABLE_RE = re.compile(
    r"^(?:(?P<schema>[A-Za-z_][A-Za-z0-9_]*)\.)?(?P<table>[A-Za-z_][A-Za-z0-9_]*)$"
)


@dataclass(frozen=True, slots=True)
class RemoteSqlProbeResult:
    columns: list[str]
    row_count: int
    schema: str
    table: str


def validate_postgres_dsn(dsn: str) -> str:
    raw = (dsn or "").strip()
    if not raw:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="remote DSN is empty",
        )
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"postgresql", "postgres"}:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="remote DSN must use postgresql:// or postgres://",
        )
    if not parsed.hostname:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="remote DSN host is required",
        )
    return raw


def parse_remote_table(raw: str) -> tuple[str, str]:
    text = (raw or "").strip()
    match = _TABLE_RE.match(text)
    if match is None:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="remote_table must be table or schema.table (identifiers only)",
        )
    schema = match.group("schema") or "public"
    table = match.group("table")
    if not _IDENT_RE.match(schema) or not _IDENT_RE.match(table):
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="remote_table identifiers are invalid",
        )
    return schema, table


def quote_ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail=f"invalid SQL identifier: {name!r}",
        )
    return '"' + name.replace('"', '""') + '"'


async def probe_remote_postgres(*, dsn: str, remote_table: str) -> RemoteSqlProbeResult:
    """Connect briefly: list columns + COUNT(*) — never SELECT *."""
    dsn = validate_postgres_dsn(dsn)
    schema, table = parse_remote_table(remote_table)
    conn: asyncpg.Connection | None = None
    try:
        conn = await asyncpg.connect(dsn=dsn, timeout=10.0, command_timeout=30.0)
        col_rows = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = $2
            ORDER BY ordinal_position
            """,
            schema,
            table,
        )
        columns = [str(r["column_name"]) for r in col_rows if r.get("column_name")]
        if not columns:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=f"table {schema}.{table} not found or has no columns",
            )
        qualified = f"{quote_ident(schema)}.{quote_ident(table)}"
        count = int(await conn.fetchval(f"SELECT COUNT(*) FROM {qualified}"))
        return RemoteSqlProbeResult(
            columns=columns,
            row_count=count,
            schema=schema,
            table=table,
        )
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            code="SERVICE_UNAVAILABLE",
            title="Service Unavailable",
            status=503,
            detail=f"remote SQL probe failed: {exc}",
        ) from exc
    finally:
        if conn is not None:
            await conn.close()


def sanitize_env_row_id(row_id: str, *, max_len: int = 40) -> str:
    """UPPER_SNAKE fragment safe for env name suffix."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", (row_id or "").strip()).strip("_").upper()
    if not cleaned:
        cleaned = "ROW"
    if cleaned[0].isdigit():
        cleaned = "R_" + cleaned
    return cleaned[:max_len]
