"""Live PostgreSQL probe for remote equipment catalogs (no data snapshot)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse, urlunparse

import asyncpg

from prodavan.domain.errors import AppError

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TABLE_RE = re.compile(
    r"^(?:(?P<schema>[A-Za-z_][A-Za-z0-9_]*)\.)?(?P<table>[A-Za-z_][A-Za-z0-9_]*)$"
)

# Default SQL relation when DSN already has /dbname and UI hides the table field.
DEFAULT_REMOTE_SQL_TABLE = "public.offers"


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


def postgres_dsn_database_name(dsn: str) -> str | None:
    """Return database name from URL path, or None when missing."""
    path = (urlparse((dsn or "").strip()).path or "").lstrip("/")
    if not path:
        return None
    name = path.split("/", 1)[0].strip()
    return name or None


def postgres_dsn_table_query(dsn: str) -> str | None:
    """Optional `?table=` / `?remote_table=` override for the SQL relation."""
    qs = parse_qs(urlparse((dsn or "").strip()).query)
    for key in ("table", "remote_table"):
        vals = qs.get(key)
        if vals and str(vals[0]).strip():
            return str(vals[0]).strip()
    return None


def _dsn_with_database(dsn: str, database: str) -> str:
    if not _IDENT_RE.match(database):
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="database name must be a simple SQL identifier",
        )
    parsed = urlparse(dsn)
    return urlunparse(parsed._replace(path=f"/{database}"))


def resolve_remote_catalog_target(
    *,
    dsn: str,
    remote_table_field: str,
    default_sql_table: str = DEFAULT_REMOTE_SQL_TABLE,
) -> tuple[str, str, str]:
    """Resolve connect DSN + schema.table for a remote catalog row.

    - Database comes from URL path ``/dbname``, else from [remote_table_field]
      when that value is a simple identifier (UI field shown only if path missing).
    - SQL relation comes from ``?table=``, else field when it looks like
      ``table`` / ``schema.table`` *and* database is already in the URL, else
      [default_sql_table].
    """
    dsn = validate_postgres_dsn(dsn)
    db = postgres_dsn_database_name(dsn)
    q_table = postgres_dsn_table_query(dsn)
    field = (remote_table_field or "").strip()

    if not db:
        if not field:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=(
                    "remote DSN must include /database "
                    "(e.g. postgresql://user:pass@host:5432/dbname) "
                    "or fill the database name field"
                ),
            )
        if "." in field:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="put /database in the DSN when specifying schema.table",
            )
        connect_dsn = _dsn_with_database(dsn, field)
        sql_raw = q_table or default_sql_table
    else:
        connect_dsn = dsn
        if q_table:
            sql_raw = q_table
        elif field:
            sql_raw = field
        else:
            sql_raw = default_sql_table

    schema, table = parse_remote_table(sql_raw)
    return connect_dsn, schema, table


def quote_ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail=f"invalid SQL identifier: {name!r}",
        )
    return '"' + name.replace('"', '""') + '"'


async def probe_remote_postgres(
    *,
    dsn: str,
    remote_table: str,
    default_sql_table: str = DEFAULT_REMOTE_SQL_TABLE,
) -> RemoteSqlProbeResult:
    """Connect briefly: list columns + COUNT(*) — never SELECT *."""
    connect_dsn, schema, table = resolve_remote_catalog_target(
        dsn=dsn,
        remote_table_field=remote_table,
        default_sql_table=default_sql_table,
    )
    conn: asyncpg.Connection | None = None
    try:
        conn = await asyncpg.connect(dsn=connect_dsn, timeout=10.0, command_timeout=30.0)
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
        host = urlparse(connect_dsn).hostname or "?"
        raise AppError(
            code="SERVICE_UNAVAILABLE",
            title="Service Unavailable",
            status=503,
            detail=(
                f"remote SQL probe failed ({host}): {exc}. "
                "From k3s-in-WSL use the Windows host gateway "
                "(e.g. 172.21.176.1), not the LAN IP; ensure /dbname is set."
            ),
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
