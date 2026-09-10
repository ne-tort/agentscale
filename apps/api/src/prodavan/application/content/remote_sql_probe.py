"""Live PostgreSQL probe for remote equipment catalogs (no data snapshot)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import asyncpg

from prodavan.domain.errors import AppError

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TABLE_RE = re.compile(
    r"^(?:(?P<schema>[A-Za-z_][A-Za-z0-9_]*)\.)?(?P<table>[A-Za-z_][A-Za-z0-9_]*)$"
)

# Query keys we parse ourselves — must never reach asyncpg/libpq.
_APP_QUERY_KEYS = frozenset({"table", "remote_table"})


@dataclass(frozen=True, slots=True)
class RemoteSqlProbeResult:
    columns: list[str]
    row_count: int
    schema: str
    table: str


@dataclass(frozen=True, slots=True)
class RemoteSqlTableInfo:
    schema: str
    table: str
    row_count: int

    @property
    def name(self) -> str:
        return f"{self.schema}.{self.table}"


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
    """Optional `?table=` / `?remote_table=` (app-level; stripped before connect)."""
    qs = dict(parse_qsl(urlparse((dsn or "").strip()).query, keep_blank_values=False))
    for key in ("table", "remote_table"):
        val = qs.get(key)
        if val and str(val).strip():
            return str(val).strip()
    return None


def strip_postgres_driver_query(dsn: str) -> str:
    """Remove app-only query keys so asyncpg/libpq never see them."""
    parsed = urlparse((dsn or "").strip())
    kept = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k not in _APP_QUERY_KEYS
    ]
    return urlunparse(parsed._replace(query=urlencode(kept)))


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


def resolve_connect_dsn(
    *,
    dsn: str,
    remote_database_field: str = "",
) -> str:
    """Resolve connect DSN (database path) without requiring a SQL table."""
    dsn = validate_postgres_dsn(dsn)
    db = postgres_dsn_database_name(dsn)
    field = (remote_database_field or "").strip()
    if db:
        return strip_postgres_driver_query(dsn)
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
            detail="database name must be a simple identifier, not schema.table",
        )
    return strip_postgres_driver_query(_dsn_with_database(dsn, field))


def resolve_remote_catalog_target(
    *,
    dsn: str,
    remote_table_field: str,
    remote_database_field: str = "",
) -> tuple[str, str, str]:
    """Resolve connect DSN + schema.table. Requires an explicit SQL table.

    SQL relation: ``?table=`` / ``?remote_table=`` (stripped before connect),
    else [remote_table_field]. No silent default to public.offers.
    """
    connect_dsn = resolve_connect_dsn(
        dsn=dsn,
        remote_database_field=remote_database_field,
    )
    q_table = postgres_dsn_table_query(dsn)
    field = (remote_table_field or "").strip()
    sql_raw = q_table or field
    if not sql_raw:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="remote_table is required (pick a table)",
        )
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


def _connect_error(dsn: str, exc: Exception) -> AppError:
    host = urlparse(dsn).hostname or "?"
    return AppError(
        code="SERVICE_UNAVAILABLE",
        title="Service Unavailable",
        status=503,
        detail=(
            f"remote SQL probe failed ({host}): {exc}. "
            "Check host reachability from the API and that the URL includes /dbname."
        ),
    )


async def check_remote_postgres_connect(*, dsn: str, remote_database_field: str = "") -> str:
    """Verify TCP/auth/database; return stripped connect DSN."""
    connect_dsn = resolve_connect_dsn(dsn=dsn, remote_database_field=remote_database_field)
    conn: asyncpg.Connection | None = None
    try:
        conn = await asyncpg.connect(dsn=connect_dsn, timeout=10.0, command_timeout=30.0)
        await conn.fetchval("SELECT 1")
        return connect_dsn
    except AppError:
        raise
    except Exception as exc:
        raise _connect_error(connect_dsn, exc) from exc
    finally:
        if conn is not None:
            await conn.close()


async def list_remote_tables(
    *,
    dsn: str,
    remote_database_field: str = "",
) -> list[RemoteSqlTableInfo]:
    """List user BASE TABLEs with exact COUNT(*) (picker)."""
    connect_dsn = resolve_connect_dsn(dsn=dsn, remote_database_field=remote_database_field)
    conn: asyncpg.Connection | None = None
    try:
        conn = await asyncpg.connect(dsn=connect_dsn, timeout=10.0, command_timeout=120.0)
        rows = await conn.fetch(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
              AND table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY table_schema, table_name
            """
        )
        out: list[RemoteSqlTableInfo] = []
        for r in rows:
            schema = str(r["table_schema"])
            table = str(r["table_name"])
            if not _IDENT_RE.match(schema) or not _IDENT_RE.match(table):
                continue
            qualified = f"{quote_ident(schema)}.{quote_ident(table)}"
            count = int(await conn.fetchval(f"SELECT COUNT(*) FROM {qualified}"))
            out.append(RemoteSqlTableInfo(schema=schema, table=table, row_count=count))
        return out
    except AppError:
        raise
    except Exception as exc:
        raise _connect_error(connect_dsn, exc) from exc
    finally:
        if conn is not None:
            await conn.close()


async def probe_remote_postgres(
    *,
    dsn: str,
    remote_table: str,
    remote_database_field: str = "",
) -> RemoteSqlProbeResult:
    """Connect briefly: list columns + COUNT(*) — never SELECT *."""
    connect_dsn, schema, table = resolve_remote_catalog_target(
        dsn=dsn,
        remote_table_field=remote_table,
        remote_database_field=remote_database_field,
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
        raise _connect_error(connect_dsn, exc) from exc
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
