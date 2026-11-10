"""Backend пилотного плагина hello_plugin (эталон v4).

Доверие строится заново на КАЖДОМ API-запросе:

* Gateway передаёт короткоживущий capability token только в
  X-Plugin-Capability-Token; backend не декодирует JWT и не хранит его в env;
* protected GET {GATEWAY_URL}/capabilities/v1/health с
  Authorization: Bearer <token> выполняет introspection и возвращает строго
  типизированный tenant/capability scope;
* только этот scope используется в SQL. Query/body company_id,
  cabinet_id и project_id — лишь утверждения вызывающей стороны,
  которые обязаны совпасть с introspection (иначе 403);
* X-Company-ID и остальные identity-заголовки клиента не читаются;
* signing secret платформы плагину не нужен и не передаётся.

Локальный /health остаётся readiness после Liquibase update. Webhook имеет
отдельный HMAC-контракт, описанный в backend/CONTRACT.md; его in-memory
идемпотентность является демонстрационной, НЕ durable production-механизмом.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import uuid
from contextvars import ContextVar
from pathlib import Path
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg
import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from runtime_api import RemoteRuntimeProviderClient, install_runtime_api

# Статический capability token здесь намеренно отсутствует: он существует
# только в конкретном request и никогда не становится env/state.
PLUGIN_ID = os.environ.get("PLUGIN_ID", "")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "")
TENANT_DB_DSN = os.environ.get("TENANT_DB_DSN", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

PROTOCOL = "plugin-rpc/1"
CAPABILITY_TOKEN_HEADER = "X-Plugin-Capability-Token"
INTROSPECTION_PATH = "/capabilities/v1/health"
INTROSPECTION_FIELDS = frozenset(
    {
        "plugin_id",
        "company_id",
        "cabinet_id",
        "project_id",
        "grant_id",
        "artifact_digest",
        "scopes",
        "exp",
    }
)

app = FastAPI(title="hello_plugin", version="0.1.0", docs_url=None, redoc_url=None)

_MIGRATIONS_OK = os.environ.get("LIQUIBASE_UPDATE_OK", "") == "1"

_pool: asyncpg.Pool | None = None
# Demo only: процессная память теряется при рестарте и не общая между репликами.
_seen_deliveries: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True, slots=True)
class CapabilityScope:
    """Проверенный introspection scope; единственный источник tenant identity."""

    plugin_id: str
    company_id: str
    cabinet_id: str | None
    project_id: str | None
    grant_id: str
    artifact_digest: str
    scopes: tuple[str, ...]
    exp: float


def _current_timestamp() -> float:
    return datetime.now(tz=UTC).timestamp()


def _require_secret_env() -> None:
    """Проверка статической конфигурации; request token сюда НЕ входит."""
    missing = [
        name
        for name, value in (
            ("GATEWAY_URL", GATEWAY_URL),
            ("PLUGIN_ID", PLUGIN_ID),
            ("TENANT_DB_DSN", TENANT_DB_DSN),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"не заданы обязательные переменные из Secret: {', '.join(missing)}")


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail)


def _forbidden(detail: str = "capability scope does not match request") -> HTTPException:
    return HTTPException(status_code=403, detail=detail)


def _introspection_unavailable() -> HTTPException:
    # Не включаем URL, response body или текст исключения: там могут быть token/secret.
    return HTTPException(status_code=503, detail="capability introspection unavailable")


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and value.strip() == value


def _optional_nonempty_string(value: Any) -> bool:
    return value is None or _nonempty_string(value)


def _parse_introspection(payload: Any) -> CapabilityScope:
    """Строгий allowlist ответа health: никаких extra/неизвестных полей."""
    if not isinstance(payload, dict) or set(payload) != INTROSPECTION_FIELDS:
        raise _introspection_unavailable()

    if not all(
        _nonempty_string(payload[field])
        for field in ("plugin_id", "company_id", "grant_id", "artifact_digest")
    ):
        raise _introspection_unavailable()
    if not _optional_nonempty_string(payload["cabinet_id"]):
        raise _introspection_unavailable()
    if not _optional_nonempty_string(payload["project_id"]):
        raise _introspection_unavailable()

    digest = payload["artifact_digest"]
    if (
        not digest.startswith("sha256:")
        or len(digest) != len("sha256:") + 64
        or any(char not in "0123456789abcdef" for char in digest[len("sha256:") :])
    ):
        raise _introspection_unavailable()

    raw_scopes = payload["scopes"]
    if (
        not isinstance(raw_scopes, list)
        or any(not _nonempty_string(scope) for scope in raw_scopes)
        or len(set(raw_scopes)) != len(raw_scopes)
    ):
        raise _introspection_unavailable()

    exp = payload["exp"]
    if isinstance(exp, bool) or not isinstance(exp, (int, float)) or not math.isfinite(exp):
        raise _introspection_unavailable()

    if payload["plugin_id"] != PLUGIN_ID:
        raise _forbidden("capability token belongs to another plugin")
    if float(exp) <= _current_timestamp():
        raise _unauthorized("capability token expired")

    return CapabilityScope(
        plugin_id=payload["plugin_id"],
        company_id=payload["company_id"],
        cabinet_id=payload["cabinet_id"],
        project_id=payload["project_id"],
        grant_id=payload["grant_id"],
        artifact_digest=digest,
        scopes=tuple(raw_scopes),
        exp=float(exp),
    )


async def _introspect_capability(token: str) -> CapabilityScope:
    """Проверить token через protected health на каждом запросе, без кэша."""
    if not GATEWAY_URL:
        raise _introspection_unavailable()

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(2.0, connect=1.0),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = await client.get(
                f"{GATEWAY_URL.rstrip('/')}{INTROSPECTION_PATH}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )
    except Exception:
        # Redirect не следуется; timeout/DNS/TLS/HTTP ошибки одинаково fail-closed.
        raise _introspection_unavailable() from None

    if response.status_code == 401:
        raise _unauthorized("capability token was rejected")
    if response.status_code == 403:
        raise _forbidden("capability token was rejected")
    if response.status_code != 200:
        raise _introspection_unavailable()

    try:
        payload = response.json()
    except Exception:
        raise _introspection_unavailable() from None
    return _parse_introspection(payload)


def _request_token(request: Request) -> str:
    """Единственный входной контракт Gateway: X-Plugin-Capability-Token."""
    token = request.headers.get(CAPABILITY_TOKEN_HEADER)
    if (
        token is None
        or not token
        or token.strip() != token
        or len(token) > 16_384
    ):
        raise _unauthorized("X-Plugin-Capability-Token required")
    return token


async def _request_scope(
    request: Request,
    *,
    company_id: Any,
    cabinet_id: Any = None,
    project_id: Any = None,
) -> CapabilityScope:
    """Introspect + сравнить client assertions с claims.

    company_id обязателен в существующем API как assertion маршрута, но не
    является SQL selector. Это сохраняет независимую проверку company-bound DSN
    после того, как Gateway срезал /pluginpage/{plugin}/{company}.
    """
    scope = await _introspect_capability(_request_token(request))

    if not _nonempty_string(company_id):
        raise HTTPException(status_code=422, detail="company_id обязателен")
    if company_id != scope.company_id:
        raise _forbidden()

    for requested, actual in (
        (cabinet_id, scope.cabinet_id),
        (project_id, scope.project_id),
    ):
        if requested is not None and (
            not _nonempty_string(requested) or requested != actual
        ):
            raise _forbidden()
    return scope


def _greetings_where(scope: CapabilityScope) -> tuple[str, list[Any]]:
    """WHERE только из introspection; значения всегда параметризованы."""
    clauses = ["company_id = $1"]
    params: list[Any] = [scope.company_id]
    for column, value in (
        ("cabinet_id", scope.cabinet_id),
        ("project_id", scope.project_id),
    ):
        if value is not None:
            params.append(value)
            clauses.append(f"{column} = $" + str(len(params)))
    return " AND ".join(clauses), params


def _stats_id(scope: CapabilityScope) -> uuid.UUID:
    """Ключ агрегата привязан к полному tenant-scope, а не к client input."""
    scope_key = json.dumps(
        [scope.company_id, scope.cabinet_id, scope.project_id],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return uuid.uuid5(uuid.NAMESPACE_OID, f"hello_plugin:stats:{scope_key}")


async def _get_pool() -> asyncpg.Pool:
    """Пул к СВОЕЙ БД компании по прямому DSN, без платформенной БД."""
    global _pool
    if _pool is None:
        if not TENANT_DB_DSN:
            raise RuntimeError("TENANT_DB_DSN не задан в Secret")
        _pool = await asyncpg.create_pool(dsn=TENANT_DB_DSN, min_size=1, max_size=5)
    return _pool


@app.on_event("startup")
async def _startup() -> None:
    _require_secret_env()
    # Пул создаём лениво; никакой introspection/token не кэшируется при старте.


@app.on_event("shutdown")
async def _shutdown() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@app.get("/health/live")
async def health_live() -> Response:
    """Bounded liveness процесса: без DB, tenant, capability и Secret access.

    Uvicorn запускается только после успешного entrypoint Liquibase update, но
    этот ответ намеренно НЕ утверждает readiness и не выполняет миграции.
    """
    return JSONResponse(status_code=200, content={"status": "alive"})


async def _readiness_response() -> Response:
    """Единая fail-closed readiness проверка для нового пути и legacy alias."""
    if not _MIGRATIONS_OK:
        return JSONResponse(status_code=503, content={"status": "migrations_pending"})
    try:
        pool = await _get_pool()
        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT to_regclass('greetings') IS NOT NULL "
                "AND to_regclass('greeting_stats') IS NOT NULL"
            )
        if not exists:
            return JSONResponse(status_code=503, content={"status": "schema_missing"})
    except Exception:  # noqa: BLE001 — любая ошибка БД = не Ready
        return JSONResponse(status_code=503, content={"status": "db_unavailable"})
    return JSONResponse(status_code=200, content={"status": "ready", "plugin_id": PLUGIN_ID})


@app.get("/health/ready")
async def health_ready() -> Response:
    """Ready только после marker успешного Liquibase update и проверки схемы."""
    return await _readiness_response()


@app.get("/health")
async def health() -> Response:
    """Backward-compatible alias с ровно той же readiness семантикой."""
    return await _readiness_response()


def _signature_ok(raw_body: bytes, signature: str) -> bool:
    """Текущий совместимый контракт: HMAC-SHA256 ТОЛЬКО по raw body.

    Timestamp/delivery/event/method/path нельзя добавить односторонне: текущий
    Gateway вычисляет HMAC именно по raw_body. См. backend/CONTRACT.md.
    """
    if not WEBHOOK_SECRET or not signature.startswith("sha256="):
        return False
    supplied = signature[len("sha256=") :]
    if len(supplied) != 64 or any(char not in "0123456789abcdef" for char in supplied):
        return False
    expected = hmac.new(WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied)


@app.post("/webhook/ping")
async def webhook_ping(
    request: Request,
    x_prodavan_signature: str | None = Header(default=None, alias="X-Prodavan-Signature"),
    x_prodavan_delivery: str | None = Header(default=None, alias="X-Prodavan-Delivery"),
    x_prodavan_event: str | None = Header(default=None, alias="X-Prodavan-Event"),
) -> Response:
    """Raw-body HMAC + demo idempotency; расширенная подпись не выдумывается."""
    raw_body = await request.body()
    if not x_prodavan_delivery:
        raise HTTPException(status_code=401, detail="missing X-Prodavan-Delivery")
    if not _signature_ok(raw_body, x_prodavan_signature or ""):
        raise HTTPException(status_code=401, detail="invalid signature")

    if x_prodavan_delivery in _seen_deliveries:
        return JSONResponse(status_code=200, content=_seen_deliveries[x_prodavan_delivery])

    result = {
        "ok": True,
        "plugin_id": PLUGIN_ID,
        "event": x_prodavan_event,
        "delivery": x_prodavan_delivery,
    }
    _seen_deliveries[x_prodavan_delivery] = result
    return JSONResponse(status_code=200, content=result)


@app.get("/api/greetings/list")
async def list_greetings(
    request: Request,
    company_id: str | None = None,
    cabinet_id: str | None = None,
    project_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Список строго в introspected company/cabinet/project scope."""
    scope = await _request_scope(
        request,
        company_id=company_id,
        cabinet_id=cabinet_id,
        project_id=project_id,
    )
    pool = await _get_pool()
    safe_limit = max(1, min(limit, 200))
    where, params = _greetings_where(scope)
    limit_placeholder = "$" + str(len(params) + 1)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, company_id, cabinet_id, project_id, message, created_at "
            f"FROM greetings WHERE {where} "
            f"ORDER BY created_at DESC LIMIT {limit_placeholder}",
            *params,
            safe_limit,
        )
    return [dict(row) for row in rows]


@app.post("/api/greetings")
async def create_greeting(request: Request, payload: dict[str, Any]) -> Response:
    """Запись и агрегат в одной транзакции и одном introspected tenant-scope."""
    scope = await _request_scope(
        request,
        company_id=payload.get("company_id"),
        cabinet_id=payload.get("cabinet_id"),
        project_id=payload.get("project_id"),
    )
    unknown = set(payload) - {"company_id", "cabinet_id", "project_id", "message"}
    if unknown:
        raise HTTPException(status_code=422, detail="неизвестные поля запроса")

    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise HTTPException(status_code=422, detail="message обязателен")

    greeting_id = uuid.uuid4()
    pool = await _get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO greetings (id, company_id, cabinet_id, project_id, message) "
            "VALUES ($1, $2, $3, $4, $5)",
            greeting_id,
            scope.company_id,
            scope.cabinet_id,
            scope.project_id,
            message,
        )
        await conn.execute(
            "INSERT INTO greeting_stats (id, greetings_count) "
            "VALUES ($1, 1) "
            "ON CONFLICT (id) DO UPDATE "
            "SET greetings_count = greeting_stats.greetings_count + 1",
            _stats_id(scope),
        )
    return JSONResponse(
        status_code=201,
        content={"id": str(greeting_id), "company_id": scope.company_id, "message": message},
    )


@app.get("/api/stats")
async def stats(
    request: Request,
    company_id: str | None = None,
    cabinet_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Агрегат по ключу полного introspected tenant-scope."""
    scope = await _request_scope(
        request,
        company_id=company_id,
        cabinet_id=cabinet_id,
        project_id=project_id,
    )
    pool = await _get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT greetings_count FROM greeting_stats WHERE id = $1",
            _stats_id(scope),
        )
    return {"company_id": scope.company_id, "greetings_count": int(count or 0)}


# Production providers use only the protected Gateway transport.  Module-local
# in-process declarations, metadata, actions or SecretStore doubles are not trust.
RUNTIME_DEPLOYMENT_IDENTITY_ROOT = Path("/etc/agentscale/plugin-runtime")
_RUNTIME_REQUEST_TOKEN: ContextVar[str | None] = ContextVar(
    "hello_runtime_request_token", default=None
)


def _read_runtime_deployment_identity() -> Mapping[str, str] | None:
    """Read non-secret actual identity from a platform-managed read-only volume."""
    values: dict[str, str] = {}
    try:
        for name in ("plugin_instance_id", "backend_digest"):
            with (RUNTIME_DEPLOYMENT_IDENTITY_ROOT / name).open(
                "r", encoding="ascii"
            ) as stream:
                raw = stream.read(258)
            if len(raw) > 257:
                return None
            values[name] = raw.strip()
    except (OSError, UnicodeError):
        return None
    return values


def _runtime_deployment_identity() -> Mapping[str, str] | None:
    return _read_runtime_deployment_identity()


async def _runtime_scope(request: Request) -> CapabilityScope:
    token = _request_token(request)
    scope = await _introspect_capability(token)
    # ContextVar is request-task local, not a cross-request token/claims cache.
    _RUNTIME_REQUEST_TOKEN.set(token)
    return scope


def _current_runtime_token() -> str:
    token = _RUNTIME_REQUEST_TOKEN.get()
    if token is None:
        raise RuntimeError("current runtime capability token unavailable")
    return token


_runtime_remote_providers = RemoteRuntimeProviderClient(
    gateway_url=GATEWAY_URL,
    token_provider=_current_runtime_token,
)


async def _runtime_egress_declaration(identity: Mapping[str, Any]) -> Any:
    return await _runtime_remote_providers.declaration(identity=identity)


async def _runtime_artifact_byte_count(identity: Mapping[str, Any]) -> int | None:
    return await _runtime_remote_providers.byte_count(identity=identity)


def _runtime_secret_store() -> Any:
    # Plaintext is never a provider response.  Only the control-plane may resolve
    # SecretStore values for its protected flow; hello has no local secret port.
    return None


install_runtime_api(
    app,
    scope_provider=_runtime_scope,
    egress_declaration_provider=_runtime_egress_declaration,
    artifact_metadata_provider=_runtime_artifact_byte_count,
    secret_store_provider=_runtime_secret_store,
    deployment_identity_provider=_runtime_deployment_identity,
    pod_action_provider=_runtime_remote_providers,
)
