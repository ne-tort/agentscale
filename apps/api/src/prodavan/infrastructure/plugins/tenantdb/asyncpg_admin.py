"""Async AdminConnectionPort backed by asyncpg pools.

The application service owns lifecycle and DDL. This module only parses the
admin DSN, binds the service's named parameters, selects the administrative or
tenant database pool, and normalises driver failures without leaking secrets.
No DSN, password, SQL text, or bind value is logged or included in exceptions.
"""

from __future__ import annotations

import asyncio
import math
import re
import ssl
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

import asyncpg

from prodavan.application.plugins.tenantdb.naming import assert_safe_identifier
from prodavan.application.plugins.tenantdb.service import AdminTarget, ExecuteResult

_ALLOWED_SCHEMES = frozenset({"postgres", "postgresql"})
_ALLOWED_TLS_MODES = frozenset({"require", "verify-ca", "verify-full"})
_DSN_QUERY_KEYS = frozenset({"sslmode", "sslrootcert", "sslcert", "sslkey"})
_PARAMETER_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")
_DOLLAR_QUOTE_RE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")
_SELECT_RE = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_DROP_DATABASE_RE = re.compile(
    r'^\s*DROP\s+DATABASE\s+IF\s+EXISTS\s+"([a-z0-9_]+)"\s*;?\s*$',
    re.IGNORECASE,
)


class TenantAdminConfigError(RuntimeError):
    """Safe typed configuration failure; reason is an internal constant."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"tenant DB admin configuration invalid ({reason})")


class TenantAdminExecutionError(RuntimeError):
    """Safe driver/pool failure with optional SQLSTATE for service mapping."""

    def __init__(self, operation: str, *, sqlstate: str = "") -> None:
        self.operation = operation
        self.sqlstate = sqlstate
        super().__init__(f"tenant DB admin operation failed ({operation})")


@dataclass(frozen=True, slots=True)
class AsyncpgAdminConfig:
    """Validated connection settings; credentials are excluded from repr."""

    host: str
    port: int
    user: str = field(repr=False)
    password: str = field(repr=False)
    database: str
    tls_mode: str = "verify-full"
    tls_ca_file: str = ""
    tls_cert_file: str = ""
    tls_key_file: str = ""
    connect_timeout_seconds: float = 10.0
    command_timeout_seconds: float = 30.0
    close_timeout_seconds: float = 10.0
    pool_min_size: int = 0
    pool_max_size: int = 4
    pool_max_inactive_lifetime_seconds: float = 300.0

    def __post_init__(self) -> None:
        if not self.host or not self.user or not self.password or not self.database:
            raise TenantAdminConfigError("URL must include host, user, password and database")
        if (
            not isinstance(self.port, int)
            or isinstance(self.port, bool)
            or not 1 <= self.port <= 65535
        ):
            raise TenantAdminConfigError("invalid PostgreSQL port")
        if self.tls_mode not in _ALLOWED_TLS_MODES:
            raise TenantAdminConfigError("TLS mode must be require, verify-ca or verify-full")
        if bool(self.tls_cert_file) != bool(self.tls_key_file):
            raise TenantAdminConfigError("TLS client certificate and key must be configured together")
        for value in (
            self.connect_timeout_seconds,
            self.command_timeout_seconds,
            self.close_timeout_seconds,
            self.pool_max_inactive_lifetime_seconds,
        ):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise TenantAdminConfigError("timeouts and pool lifetime must be finite and positive")
        if (
            not isinstance(self.pool_min_size, int)
            or isinstance(self.pool_min_size, bool)
            or not isinstance(self.pool_max_size, int)
            or isinstance(self.pool_max_size, bool)
            or self.pool_min_size < 0
            or self.pool_max_size < 1
            or self.pool_min_size > self.pool_max_size
        ):
            raise TenantAdminConfigError("invalid pool size limits")

    @classmethod
    def from_dsn(
        cls,
        dsn: str,
        *,
        tls_mode: str = "verify-full",
        tls_ca_file: str = "",
        tls_cert_file: str = "",
        tls_key_file: str = "",
        connect_timeout_seconds: float = 10.0,
        command_timeout_seconds: float = 30.0,
        close_timeout_seconds: float = 10.0,
        pool_min_size: int = 0,
        pool_max_size: int = 4,
        pool_max_inactive_lifetime_seconds: float = 300.0,
    ) -> "AsyncpgAdminConfig":
        """Parse one PostgreSQL URI without ever reflecting its secret parts."""
        if not isinstance(dsn, str) or not dsn.strip():
            raise TenantAdminConfigError("PLUGIN_TENANT_DB_ADMIN_URL is required")
        try:
            parsed = urlsplit(dsn.strip())
            if parsed.scheme.lower() not in _ALLOWED_SCHEMES or parsed.fragment:
                raise TenantAdminConfigError("unsupported PostgreSQL URL")
            host = parsed.hostname or ""
            port = parsed.port or 5432
            user = unquote(parsed.username or "", errors="strict")
            password = unquote(parsed.password or "", errors="strict")
            raw_path = parsed.path[1:] if parsed.path.startswith("/") else parsed.path
            database = unquote(raw_path, errors="strict") if raw_path else "postgres"
            if not host or "," in host or not user or not password:
                raise TenantAdminConfigError("URL must include one host, user and password")
            if not database or "/" in database or database in {".", ".."}:
                raise TenantAdminConfigError("invalid administrative database")
            if database == "prodavan":
                raise TenantAdminConfigError("platform database cannot be tenant admin database")
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
            if set(query) - _DSN_QUERY_KEYS or any(len(values) != 1 for values in query.values()):
                raise TenantAdminConfigError("unsupported or repeated DSN query parameter")
            mode = query.get("sslmode", [tls_mode])[0].strip().lower()
            if mode not in _ALLOWED_TLS_MODES:
                raise TenantAdminConfigError("TLS disable and non-TLS fallback are forbidden")
            return cls(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                tls_mode=mode,
                tls_ca_file=query.get("sslrootcert", [tls_ca_file])[0],
                tls_cert_file=query.get("sslcert", [tls_cert_file])[0],
                tls_key_file=query.get("sslkey", [tls_key_file])[0],
                connect_timeout_seconds=connect_timeout_seconds,
                command_timeout_seconds=command_timeout_seconds,
                close_timeout_seconds=close_timeout_seconds,
                pool_min_size=pool_min_size,
                pool_max_size=pool_max_size,
                pool_max_inactive_lifetime_seconds=pool_max_inactive_lifetime_seconds,
            )
        except TenantAdminConfigError:
            raise
        except (TypeError, ValueError, UnicodeError):
            raise TenantAdminConfigError("malformed PostgreSQL URL") from None

    @classmethod
    def from_settings(cls, settings: Any) -> "AsyncpgAdminConfig":
        """Project typed Settings, also accepting structural test fakes."""
        try:
            raw_url = getattr(settings, "plugin_tenant_db_admin_url", "")
            get_secret_value = getattr(raw_url, "get_secret_value", None)
            if callable(get_secret_value):
                raw_url = get_secret_value()
            return cls.from_dsn(
                raw_url,
                tls_mode=str(getattr(settings, "plugin_tenant_db_admin_tls_mode", "verify-full")),
                tls_ca_file=str(getattr(settings, "plugin_tenant_db_admin_tls_ca_file", "") or ""),
                tls_cert_file=str(getattr(settings, "plugin_tenant_db_admin_tls_cert_file", "") or ""),
                tls_key_file=str(getattr(settings, "plugin_tenant_db_admin_tls_key_file", "") or ""),
                connect_timeout_seconds=float(
                    getattr(settings, "plugin_tenant_db_admin_connect_timeout_seconds", 10.0)
                ),
                command_timeout_seconds=float(
                    getattr(settings, "plugin_tenant_db_admin_command_timeout_seconds", 30.0)
                ),
                close_timeout_seconds=float(
                    getattr(settings, "plugin_tenant_db_admin_close_timeout_seconds", 10.0)
                ),
                pool_min_size=int(getattr(settings, "plugin_tenant_db_admin_pool_min_size", 0)),
                pool_max_size=int(getattr(settings, "plugin_tenant_db_admin_pool_max_size", 4)),
                pool_max_inactive_lifetime_seconds=float(
                    getattr(settings, "plugin_tenant_db_admin_pool_max_inactive_lifetime_seconds", 300.0)
                ),
            )
        except TenantAdminConfigError:
            raise
        except (TypeError, ValueError, OverflowError):
            raise TenantAdminConfigError("invalid typed admin setting") from None

    def create_ssl_context(self) -> ssl.SSLContext:
        """Build an explicit TLS context; missing credentials fail closed."""
        try:
            if self.tls_mode == "require":
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                if self.tls_ca_file:
                    context.load_verify_locations(cafile=self.tls_ca_file)
                    context.verify_mode = ssl.CERT_REQUIRED
            else:
                context = ssl.create_default_context(
                    ssl.Purpose.SERVER_AUTH,
                    cafile=self.tls_ca_file or None,
                )
                context.verify_mode = ssl.CERT_REQUIRED
                context.check_hostname = self.tls_mode == "verify-full"
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            if self.tls_cert_file:
                context.load_cert_chain(self.tls_cert_file, self.tls_key_file)
            return context
        except (OSError, ValueError, ssl.SSLError):
            raise TenantAdminConfigError("TLS credentials could not be loaded") from None


class AsyncpgAdminConnection:
    """One lifecycle-owned cluster pool plus lazily opened tenant pools."""

    def __init__(self, config: AsyncpgAdminConfig, *, pool_factory: Any = None) -> None:
        self._config = config
        self._ssl = config.create_ssl_context()
        self._pool_factory = pool_factory or asyncpg.create_pool
        self._cluster_pool: Any = None
        self._tenant_pools: dict[str, Any] = {}
        self._pool_lock = asyncio.Lock()
        self._closed = False

    @classmethod
    def from_settings(cls, settings: Any, *, pool_factory: Any = None) -> "AsyncpgAdminConnection":
        return cls(AsyncpgAdminConfig.from_settings(settings), pool_factory=pool_factory)

    @staticmethod
    def _tenant_database(target: AdminTarget) -> str:
        database = assert_safe_identifier(target.database, operation="asyncpg_admin_target")
        if not database.startswith("tenant_") or database == "tenant_":
            from prodavan.application.plugins.tenantdb.errors import TenantDbError

            raise TenantDbError.invalid_identifier("asyncpg_admin_target", kind="tenant database")
        return database

    async def _new_pool(self, database: str) -> Any:
        return await self._pool_factory(
            host=self._config.host,
            port=self._config.port,
            user=self._config.user,
            password=self._config.password,
            database=database,
            ssl=self._ssl,
            timeout=self._config.connect_timeout_seconds,
            command_timeout=self._config.command_timeout_seconds,
            min_size=self._config.pool_min_size,
            max_size=self._config.pool_max_size,
            max_inactive_connection_lifetime=self._config.pool_max_inactive_lifetime_seconds,
            server_settings={"application_name": "agentscale-tenant-admin"},
        )

    async def open(self) -> None:
        """Open and ping the administrative pool once during app startup."""
        if self._closed:
            raise TenantAdminExecutionError("open closed adapter")
        if self._cluster_pool is not None:
            return
        pool: Any = None
        try:
            pool = await self._new_pool(self._config.database)
            await pool.execute("SELECT 1", timeout=self._config.command_timeout_seconds)
            self._cluster_pool = pool
        except TenantAdminExecutionError:
            raise
        except Exception as exc:
            if pool is not None:
                try:
                    await asyncio.wait_for(pool.close(), timeout=self._config.close_timeout_seconds)
                except Exception:
                    pass
            raise TenantAdminExecutionError(
                "open administrative pool",
                sqlstate=str(getattr(exc, "sqlstate", "") or ""),
            ) from None

    async def _pool_for(self, target: AdminTarget) -> Any:
        if self._closed or self._cluster_pool is None:
            raise TenantAdminExecutionError("adapter is not open")
        if target.scope == "cluster":
            return self._cluster_pool
        database = self._tenant_database(target)
        async with self._pool_lock:
            if self._closed:
                raise TenantAdminExecutionError("adapter is closed")
            pool = self._tenant_pools.get(database)
            if pool is not None:
                return pool
            try:
                pool = await self._new_pool(database)
            except Exception as exc:
                raise TenantAdminExecutionError(
                    "open tenant pool",
                    sqlstate=str(getattr(exc, "sqlstate", "") or ""),
                ) from None
            self._tenant_pools[database] = pool
            return pool

    async def execute(
        self,
        text: str,
        params: Mapping[str, Any] | None = None,
        *,
        target: AdminTarget,
    ) -> ExecuteResult:
        """Execute service SQL with true asyncpg binds, never value formatting."""
        try:
            query, args = _compile_named_parameters(text, params)
            pool = await self._pool_for(target)
            drop_match = _DROP_DATABASE_RE.fullmatch(text)
            if drop_match is not None:
                await self._discard_tenant_pool(drop_match.group(1))
            async with pool.acquire(timeout=self._config.connect_timeout_seconds) as connection:
                if _SELECT_RE.match(text):
                    records = await connection.fetch(
                        query, *args, timeout=self._config.command_timeout_seconds
                    )
                    return ExecuteResult(rows=tuple(tuple(record) for record in records))
                await connection.execute(
                    query, *args, timeout=self._config.command_timeout_seconds
                )
                return ExecuteResult()
        except TenantAdminExecutionError:
            raise
        except Exception as exc:
            from prodavan.application.plugins.tenantdb.errors import TenantDbError

            if isinstance(exc, TenantDbError):
                raise
            raise TenantAdminExecutionError(
                "execute tenant lifecycle statement",
                sqlstate=str(getattr(exc, "sqlstate", "") or ""),
            ) from None

    async def _discard_tenant_pool(self, database: str) -> None:
        async with self._pool_lock:
            pool = self._tenant_pools.pop(database, None)
        if pool is not None:
            await asyncio.wait_for(pool.close(), timeout=self._config.close_timeout_seconds)

    async def close(self) -> None:
        """Idempotently close every pool; no per-request pool is created."""
        if self._closed:
            return
        self._closed = True
        async with self._pool_lock:
            pools = [*self._tenant_pools.values()]
            if self._cluster_pool is not None:
                pools.append(self._cluster_pool)
            self._tenant_pools.clear()
            self._cluster_pool = None
        results = await asyncio.gather(
            *(
                asyncio.wait_for(pool.close(), timeout=self._config.close_timeout_seconds)
                for pool in pools
            ),
            return_exceptions=True,
        )
        if any(isinstance(result, BaseException) for result in results):
            raise TenantAdminExecutionError("close administrative pools")

    async def health(self) -> bool:
        """Readiness requires an open adapter and a working administrative pool."""
        if self._closed or self._cluster_pool is None:
            return False
        try:
            await self.execute("SELECT 1", target=AdminTarget(scope="cluster"))
            return True
        except Exception:
            return False


def _compile_named_parameters(
    text: str,
    params: Mapping[str, Any] | None,
) -> tuple[str, tuple[Any, ...]]:
    """Translate :name outside literals/comments to asyncpg $n binds."""
    if not isinstance(text, str) or not text.strip():
        raise TenantAdminExecutionError("bind empty statement")
    values = dict(params or {})
    output: list[str] = []
    args: list[Any] = []
    positions: dict[str, int] = {}
    used: set[str] = set()
    state = "normal"
    block_depth = 0
    dollar_quote = ""
    index = 0
    size = len(text)

    while index < size:
        char = text[index]
        following = text[index + 1] if index + 1 < size else ""
        if state == "single":
            output.append(char)
            index += 1
            if char == "'":
                if following == "'":
                    output.append(following)
                    index += 1
                else:
                    state = "normal"
            continue
        if state == "double":
            output.append(char)
            index += 1
            if char == '"':
                if following == '"':
                    output.append(following)
                    index += 1
                else:
                    state = "normal"
            continue
        if state == "line_comment":
            output.append(char)
            index += 1
            if char == "\n":
                state = "normal"
            continue
        if state == "block_comment":
            if text.startswith("/*", index):
                output.append("/*")
                block_depth += 1
                index += 2
            elif text.startswith("*/", index):
                output.append("*/")
                block_depth -= 1
                index += 2
                if block_depth == 0:
                    state = "normal"
            else:
                output.append(char)
                index += 1
            continue
        if state == "dollar_quote":
            if text.startswith(dollar_quote, index):
                output.append(dollar_quote)
                index += len(dollar_quote)
                state = "normal"
            else:
                output.append(char)
                index += 1
            continue

        if text.startswith("--", index):
            output.append("--")
            index += 2
            state = "line_comment"
            continue
        if text.startswith("/*", index):
            output.append("/*")
            index += 2
            block_depth = 1
            state = "block_comment"
            continue
        if char in {"'", '"'}:
            output.append(char)
            index += 1
            state = "single" if char == "'" else "double"
            continue
        if char == "$":
            match = _DOLLAR_QUOTE_RE.match(text, index)
            if match is not None:
                dollar_quote = match.group(0)
                output.append(dollar_quote)
                index = match.end()
                state = "dollar_quote"
                continue
            if following.isdigit():
                raise TenantAdminExecutionError("mixed positional and named binds")
        if char == ":" and following == ":":
            output.append("::")
            index += 2
            continue
        if char == ":":
            match = _PARAMETER_RE.match(text, index)
            if match is not None:
                name = match.group(1)
                if name not in values:
                    raise TenantAdminExecutionError("missing named bind")
                if name not in positions:
                    positions[name] = len(args) + 1
                    args.append(values[name])
                used.add(name)
                output.append("$" + str(positions[name]))
                index = match.end()
                continue
        output.append(char)
        index += 1

    if state in {"single", "double", "block_comment", "dollar_quote"}:
        raise TenantAdminExecutionError("unterminated SQL literal or comment")
    if used != set(values):
        raise TenantAdminExecutionError("unused named bind")
    return "".join(output), tuple(args)


__all__ = [
    "AsyncpgAdminConfig",
    "AsyncpgAdminConnection",
    "TenantAdminConfigError",
    "TenantAdminExecutionError",
]
