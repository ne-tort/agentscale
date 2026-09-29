"""Live PostgreSQL probe for remote equipment catalogs (no data snapshot)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlunparse

import asyncpg

from prodavan.domain.errors import AppError

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Postgres DB names commonly include hyphens; allow them for connect/list.
_DB_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$")
_TABLE_RE = re.compile(
    r"^(?:(?P<schema>[A-Za-z_][A-Za-z0-9_]*)\.)?(?P<table>[A-Za-z_][A-Za-z0-9_]*)$"
)

# Query keys we parse ourselves — must never reach asyncpg/libpq.
_APP_QUERY_KEYS = frozenset({"table", "remote_table"})
_MAINTENANCE_DATABASE = "postgres"


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


@dataclass(frozen=True, slots=True)
class RemoteSqlDatabaseInfo:
    name: str


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


def looks_like_remote_table(raw: str) -> bool:
    """True when value is table or schema.table (not a bare database name with a dot)."""
    text = (raw or "").strip()
    if not text or "." not in text:
        return False
    return _TABLE_RE.match(text) is not None


def is_simple_database_name(raw: str) -> bool:
    return bool(_DB_NAME_RE.match((raw or "").strip()))


def postgres_dsn_database_name(dsn: str) -> str | None:
    """Return database name from URL path, or None when missing/misfiled as schema.table."""
    path = (urlparse((dsn or "").strip()).path or "").lstrip("/")
    if not path:
        return None
    name = unquote(path.split("/", 1)[0].strip())
    if not name:
        return None
    if looks_like_remote_table(name):
        return None
    return name


def postgres_dsn_path_as_table(dsn: str) -> str | None:
    """If URL path looks like schema.table, return it as a SQL relation hint."""
    path = (urlparse((dsn or "").strip()).path or "").lstrip("/")
    if not path:
        return None
    name = path.split("/", 1)[0].strip()
    return name if looks_like_remote_table(name) else None


def postgres_dsn_has_user(dsn: str) -> bool:
    user = urlparse((dsn or "").strip()).username
    return bool(user and unquote(user))


def postgres_dsn_has_password(dsn: str) -> bool:
    parsed = urlparse((dsn or "").strip())
    return parsed.password is not None and parsed.password != ""


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


def _dsn_clear_path(dsn: str) -> str:
    parsed = urlparse(dsn)
    return urlunparse(parsed._replace(path=""))


def _rebuild_netloc(
    *,
    hostname: str,
    port: int | None,
    username: str | None,
    password: str | None,
) -> str:
    host = hostname
    if port is not None:
        host = f"{hostname}:{port}"
    if username is None:
        return host
    user = quote(username, safe="")
    if password is None:
        return f"{user}@{host}"
    return f"{user}:{quote(password, safe='')}@{host}"


def apply_remote_connect_overrides(
    dsn: str,
    *,
    database: str = "",
    user: str = "",
    password: str = "",
    for_database_list: bool = False,
) -> str:
    """Build asyncpg DSN: URL creds/path plus field overlays; strip app query keys.

    When ``for_database_list`` is True and no target database is known, connect to
    the maintenance database ``postgres``.
    """
    dsn = validate_postgres_dsn(dsn)
    cleaned = strip_postgres_driver_query(dsn)
    if postgres_dsn_path_as_table(cleaned):
        cleaned = _dsn_clear_path(cleaned)
    parsed = urlparse(cleaned)

    url_user = unquote(parsed.username) if parsed.username else None
    url_password = unquote(parsed.password) if parsed.password is not None else None
    overlay_user = (user or "").strip() or None
    overlay_password = (password or "").strip() or None

    final_user = overlay_user or url_user
    final_password = overlay_password if overlay_password is not None else url_password
    if overlay_user and overlay_password is None and url_password is not None:
        final_password = url_password

    hostname = parsed.hostname or ""
    netloc = _rebuild_netloc(
        hostname=hostname,
        port=parsed.port,
        username=final_user,
        password=final_password if final_user is not None else None,
    )

    db_field = unquote((database or "").strip())
    if looks_like_remote_table(db_field):
        db_field = ""
    path_db = postgres_dsn_database_name(cleaned)
    if for_database_list and not db_field and not path_db:
        target_db = _MAINTENANCE_DATABASE
    elif db_field:
        if not is_simple_database_name(db_field):
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=(
                    "database name must be a simple identifier "
                    "(letters, digits, underscore, hyphen; e.g. remote_catalog), "
                    "not a URL or schema.table"
                ),
            )
        target_db = db_field
    elif path_db:
        if not is_simple_database_name(path_db):
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=(
                    f"database name in URL path is invalid: {path_db!r}. "
                    "Use a simple identifier (e.g. /remote_catalog)."
                ),
            )
        target_db = path_db
    else:
        target_db = ""

    path = f"/{target_db}" if target_db else ""
    return urlunparse((parsed.scheme, netloc, path, "", parsed.query, ""))


def normalize_remote_db_and_table(
    *,
    dsn: str,
    remote_database_field: str = "",
    remote_table_field: str = "",
) -> tuple[str, str]:
    """Return (database_name_or_empty, sql_table).

    Mis-filed ``schema.table`` in the database field (or URL path) is moved to the
    table slot. SoT for the SQL relation remains ``remote_table`` / ``?table=``.
    """
    db = (remote_database_field or "").strip()
    table = (remote_table_field or "").strip()
    if looks_like_remote_table(db):
        if not table:
            table = db
        db = ""
    path_table = postgres_dsn_path_as_table(dsn)
    if path_table and not table:
        table = path_table
    q_table = postgres_dsn_table_query(dsn)
    if q_table and not table:
        table = q_table
    path_db = postgres_dsn_database_name(dsn)
    if path_db:
        db = path_db
    return db, table


def resolve_connect_dsn(
    *,
    dsn: str,
    remote_database_field: str = "",
    remote_user_field: str = "",
    remote_password_field: str = "",
    for_database_list: bool = False,
    require_database: bool = True,
) -> str:
    """Resolve connect DSN with optional user/password/database overlays."""
    dsn = validate_postgres_dsn(dsn)
    db, _table = normalize_remote_db_and_table(
        dsn=dsn,
        remote_database_field=remote_database_field,
    )
    field = (remote_database_field or "").strip()
    if looks_like_remote_table(field):
        field = ""
    if not db and field and is_simple_database_name(field):
        db = field

    if require_database and not db and not for_database_list:
        if looks_like_remote_table((remote_database_field or "").strip()):
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail=(
                    "database name must be a simple identifier (e.g. remote_catalog), "
                    "not schema.table — pick the SQL table in the Table field"
                ),
            )
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail=(
                "remote DSN must include /database "
                "(e.g. postgresql://user:pass@host:5432/dbname) "
                "or pick a database"
            ),
        )

    return apply_remote_connect_overrides(
        dsn,
        database=db,
        user=remote_user_field,
        password=remote_password_field,
        for_database_list=for_database_list or (not db and not require_database),
    )


def resolve_remote_catalog_target(
    *,
    dsn: str,
    remote_table_field: str,
    remote_database_field: str = "",
    remote_user_field: str = "",
    remote_password_field: str = "",
) -> tuple[str, str, str]:
    """Resolve connect DSN + schema.table. Requires an explicit SQL table."""
    _db, sql_raw = normalize_remote_db_and_table(
        dsn=dsn,
        remote_database_field=remote_database_field,
        remote_table_field=remote_table_field,
    )
    connect_dsn = resolve_connect_dsn(
        dsn=dsn,
        remote_database_field=remote_database_field
        if not looks_like_remote_table(remote_database_field)
        else "",
        remote_user_field=remote_user_field,
        remote_password_field=remote_password_field,
    )
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
    if is_remote_auth_failure(exc):
        return AppError(
            code="REMOTE_AUTH_FAILED",
            title="Authentication Failed",
            status=422,
            detail=(
                f"invalid credentials for {host}. "
                "Enter login and password, then try again."
            ),
        )
    text = f"{type(exc).__name__} {exc}".lower()
    if any(
        marker in text
        for marker in (
            "connection_lost",
            "connection reset",
            "broken pipe",
            "network is unreachable",
            "no route to host",
            "timed out",
            "timeout",
            "connection refused",
        )
    ):
        return AppError(
            code="SERVICE_UNAVAILABLE",
            title="Service Unavailable",
            status=503,
            detail=(
                f"remote SQL probe failed ({host}): host unreachable from the API "
                f"({exc}). Check that Postgres accepts connections from the cluster "
                "network (not only localhost/LAN to your PC), firewall, and DSN."
            ),
        )
    return AppError(
        code="SERVICE_UNAVAILABLE",
        title="Service Unavailable",
        status=503,
        detail=(
            f"remote SQL probe failed ({host}): {exc}. "
            "Check host reachability from the API and credentials / database name."
        ),
    )


def is_remote_auth_failure(exc: BaseException) -> bool:
    """True for Postgres password / auth failures (show login fields, not status=error)."""
    text = f"{type(exc).__name__} {exc}".lower()
    return any(
        marker in text
        for marker in (
            "password authentication failed",
            "28p01",
            "authentication failed",
            "invalidpassword",
            "invalidauthorization",
            "no password supplied",
        )
    )


async def check_remote_postgres_connect(
    *,
    dsn: str,
    remote_database_field: str = "",
    remote_user_field: str = "",
    remote_password_field: str = "",
    allow_missing_database: bool = False,
) -> str:
    """Verify TCP/auth/(database); return stripped connect DSN."""
    missing_db = not (
        postgres_dsn_database_name(dsn) or is_simple_database_name(remote_database_field)
    )
    connect_dsn = resolve_connect_dsn(
        dsn=dsn,
        remote_database_field=remote_database_field,
        remote_user_field=remote_user_field,
        remote_password_field=remote_password_field,
        require_database=not allow_missing_database,
        for_database_list=allow_missing_database and missing_db,
    )
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


async def list_remote_databases(
    *,
    dsn: str,
    remote_user_field: str = "",
    remote_password_field: str = "",
) -> list[RemoteSqlDatabaseInfo]:
    """List non-template databases (connect via maintenance DB ``postgres``)."""
    connect_dsn = resolve_connect_dsn(
        dsn=dsn,
        remote_user_field=remote_user_field,
        remote_password_field=remote_password_field,
        for_database_list=True,
        require_database=False,
    )
    conn: asyncpg.Connection | None = None
    try:
        conn = await asyncpg.connect(dsn=connect_dsn, timeout=10.0, command_timeout=30.0)
        rows = await conn.fetch(
            """
            SELECT datname
            FROM pg_database
            WHERE datistemplate = false
            ORDER BY datname
            """
        )
        out: list[RemoteSqlDatabaseInfo] = []
        for r in rows:
            name = str(r["datname"] or "").strip()
            if is_simple_database_name(name):
                out.append(RemoteSqlDatabaseInfo(name=name))
        return out
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
    remote_user_field: str = "",
    remote_password_field: str = "",
) -> list[RemoteSqlTableInfo]:
    """List user BASE TABLEs with exact COUNT(*) (picker)."""
    connect_dsn = resolve_connect_dsn(
        dsn=dsn,
        remote_database_field=remote_database_field,
        remote_user_field=remote_user_field,
        remote_password_field=remote_password_field,
    )
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
    remote_user_field: str = "",
    remote_password_field: str = "",
) -> RemoteSqlProbeResult:
    """Connect briefly: list columns + COUNT(*) — never SELECT *."""
    connect_dsn, schema, table = resolve_remote_catalog_target(
        dsn=dsn,
        remote_table_field=remote_table,
        remote_database_field=remote_database_field,
        remote_user_field=remote_user_field,
        remote_password_field=remote_password_field,
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
