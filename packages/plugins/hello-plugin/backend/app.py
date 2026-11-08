"""Backend пилотного плагина ``hello_plugin`` (эталон v4).

Контур (§2, §5.1, §7.2–7.5 архитектуры):
  * контейнер стартует через ``entrypoint.sh``: Liquibase ``update`` (своя БД
    компании по ``TENANT_DB_DSN``) -> только после успеха
    ``exec python -m uvicorn app:app``;
  * ``/health`` = 200/Ready ТОЛЬКО после успешного ``update``, иначе 503
    (§11 критерий 4, checksum mismatch не даёт стать Ready);
  * остальная инфраструктура — НЕ напрямую, а через Plugin Gateway по
    ``GATEWAY_URL`` и ``plugin-rpc/1``/``PluginClaims``. Обращений к
    pod-gateway agent-surface НЕТ и быть не может: это trust-контур агентов
    (§4.1, §13.3), плагин company-scoped;
  * все env (``GATEWAY_URL``, ``PLUGIN_ID``, ``TENANT_DB_DSN``,
    ``PLUGIN_CLAIMS``, ``PLUGIN_WEBHOOK_SECRET``) приходят из k8s Secret,
    plaintext в манифесте/коде запрещён (§5.5);
  * ``:latest`` запрещён — образ пинится по digest задачей 16.

Fail-closed: отсутствие Secret, невалидный claims, отсутствие DSN или ошибка
БД = явная ошибка, а не «молчаливый успех» с дефолтами.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Конфигурация из Secret. Никаких dev-фолбэков с секретами: пусто => отказ.
# ---------------------------------------------------------------------------

PLUGIN_ID = os.environ.get("PLUGIN_ID", "")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "")
TENANT_DB_DSN = os.environ.get("TENANT_DB_DSN", "")
PLUGIN_CLAIMS_RAW = os.environ.get("PLUGIN_CLAIMS", "")
WEBHOOK_SECRET = os.environ.get("PLUGIN_WEBHOOK_SECRET", "")

# Идентификатор протокола frontend handshake — одна и та же константа в manifest,
# frontend и здесь (§5.2, dynamic-ui-implementation-contract.md 4.2).
PROTOCOL = "plugin-rpc/1"

app = FastAPI(title="hello_plugin", version="0.1.0", docs_url=None, redoc_url=None)

# Признак готовности выставляет entrypoint ТОЛЬКО после успешного Liquibase update.
# В самом процессе приложения он не переключается: если контейнер запущен, значит
# миграция уже прошла. Оставлен явным флагом для unit-тестов контракта /health.
_MIGRATIONS_OK = os.environ.get("LIQUIBASE_UPDATE_OK", "") == "1"

# Пул соединений и in-memory реестр доставок (демо-идемпотентность). В проде
# идемпотентность webhook'ов — outbox ``webhook_deliveries`` + ``X-Prodavan-Delivery``
# как ключ (§7.5), без дублирования обработки при повторе.
_pool: asyncpg.Pool | None = None
_seen_deliveries: dict[str, dict[str, Any]] = {}


def _require_secret_env() -> None:
    """Проверка обязательных env из Secret. Fail-closed, без plaintext-фолбэков."""
    missing = [
        name
        for name, value in (
            ("GATEWAY_URL", GATEWAY_URL),
            ("PLUGIN_ID", PLUGIN_ID),
            ("TENANT_DB_DSN", TENANT_DB_DSN),
            ("PLUGIN_CLAIMS", PLUGIN_CLAIMS_RAW),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"не заданы обязательные переменные из Secret: {', '.join(missing)}")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify_plugin_claims(raw: str | None = None) -> dict[str, Any]:
    """Проверить ``PluginClaims`` (§4.2, §7.2: backend обязан проверить JWT).

    Пилот не изобретает криптографию: подпись/JWKS/revocation ведёт задача 02,
    а здесь проверяется структурный контракт (iss/aud/typ/sub/plugin_id, exp,
    grant_id, scopes) и совпадение ``plugin_id`` с нашим ``PLUGIN_ID``.
    Невалидный/просроченный/чужой claims -> ValueError (fail-closed).
    """
    token = raw if raw is not None else PLUGIN_CLAIMS_RAW
    if not token:
        raise ValueError("PLUGIN_CLAIMS отсутствует в Secret")
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("PLUGIN_CLAIMS не является JWT")
    try:
        claims = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("payload PLUGIN_CLAIMS не разбирается") from exc
    if not isinstance(claims, dict):
        raise ValueError("payload PLUGIN_CLAIMS не объект")

    required = ("iss", "aud", "typ", "sub", "plugin_id", "grant_id", "exp", "scopes")
    if any(key not in claims for key in required):
        raise ValueError("PLUGIN_CLAIMS не содержит обязательных полей")
    if claims["iss"] != "agentscale-plugins":
        raise ValueError("неверный iss PLUGIN_CLAIMS")
    if claims["aud"] != "capability-gateway" or claims["typ"] != "plugin_capability":
        raise ValueError("неверный aud/typ PLUGIN_CLAIMS")
    if claims["plugin_id"] != PLUGIN_ID:
        raise ValueError("plugin_id в PLUGIN_CLAIMS не совпадает с PLUGIN_ID")
    if not claims["sub"].startswith(f"plugin:{PLUGIN_ID}:"):
        raise ValueError("sub PLUGIN_CLAIMS не привязан к этому плагину")
    if not isinstance(claims["scopes"], list):
        raise ValueError("scopes PLUGIN_CLAIMS должен быть списком")
    exp = claims["exp"]
    if not isinstance(exp, (int, float)) or datetime.fromtimestamp(exp, tz=UTC) <= datetime.now(tz=UTC):
        raise ValueError("PLUGIN_CLAIMS просрочен")
    return claims


def _authorized_gateway_call() -> None:
    """Любой выход в инфраструктуру начинается с проверки claims.

    Реальный HTTP-вызов к ``GATEWAY_URL``/``/capabilities/v1/*`` делает задача 11;
    здесь фиксируется, что плагин НЕ ходит в агентский pod-gateway и не доверяет
    Gateway «просто»: сначала проверка claims.
    """
    if not GATEWAY_URL:
        raise RuntimeError("GATEWAY_URL не задан в Secret")
    verify_plugin_claims()


async def _get_pool() -> asyncpg.Pool:
    """Пул к СВОЕЙ БД компании по прямому DSN (§2, вариант B, ADR-007).

    Никакого ``prodavan`` и никаких чужих DSN: граница обеспечивается ролью
    PostgreSQL + CONNECT + search_path + NetworkPolicy, а не этим кодом.
    """
    global _pool
    if _pool is None:
        if not TENANT_DB_DSN:
            raise RuntimeError("TENANT_DB_DSN не задан в Secret")
        _pool = await asyncpg.create_pool(dsn=TENANT_DB_DSN, min_size=1, max_size=5)
    return _pool


@app.on_event("startup")
async def _startup() -> None:
    # Fail-closed: без Secret и валидного claims приложение не считается рабочим.
    _require_secret_env()
    verify_plugin_claims()
    # Пул создаём лениво: старт не должен падать, если tenant-PG ещё не готов,
    # но /health всё равно остаётся 503 до LIQUIBASE_UPDATE_OK.


@app.on_event("shutdown")
async def _shutdown() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@app.get("/health")
async def health() -> Response:
    """Ready ТОЛЬКО после успешного Liquibase ``update`` (§3.1, §11 кр. 4).

    Проверка ``LIQUIBASE_UPDATE_OK`` выставляется entrypoint'ом после ``update``;
    при ошибке миграции/checksum mismatch процесс приложения не запускается,
    readinessProbe получает 503 или connection-refused — но не Ready.
    """
    if not _MIGRATIONS_OK:
        return JSONResponse(status_code=503, content={"status": "migrations_pending"})
    try:
        pool = await _get_pool()
        # Проверяем, что схема реально применена, а не только флаг env.
        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT to_regclass('greetings') IS NOT NULL "
                "AND to_regclass('greeting_stats') IS NOT NULL"
            )
        if not exists:
            return JSONResponse(status_code=503, content={"status": "schema_missing"})
    except Exception:  # noqa: BLE001 — fail-closed: любая ошибка БД = не Ready
        return JSONResponse(status_code=503, content={"status": "db_unavailable"})
    return JSONResponse(status_code=200, content={"status": "ready", "plugin_id": PLUGIN_ID})


def _signature_ok(raw_body: bytes, signature: str) -> bool:
    """``X-Prodavan-Signature: sha256=<hmac>``, ``HMAC(secret, raw_body)`` (§7.5)."""
    if not WEBHOOK_SECRET or not signature.startswith("sha256="):
        return False
    expected = hmac.new(WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature[len("sha256=") :])


@app.post("/webhook/ping")
async def webhook_ping(
    request: Request,
    x_prodavan_signature: str | None = Header(default=None, alias="X-Prodavan-Signature"),
    x_prodavan_delivery: str | None = Header(default=None, alias="X-Prodavan-Delivery"),
    x_prodavan_event: str | None = Header(default=None, alias="X-Prodavan-Event"),
) -> Response:
    """Входящий webhook: подпись + идемпотентность по ``X-Prodavan-Delivery``.

    Без подписи / с неверной подписью -> 401 (§11 кр. 7). Повтор delivery ->
    200 без повторной обработки (защита от ретраев outbox).
    """
    raw_body = await request.body()
    if not x_prodavan_delivery:
        raise HTTPException(status_code=401, detail="missing X-Prodavan-Delivery")
    if not _signature_ok(raw_body, x_prodavan_signature or ""):
        raise HTTPException(status_code=401, detail="invalid signature")

    if x_prodavan_delivery in _seen_deliveries:
        # Повтор: возвращаем сохранённый результат, обработку НЕ дублируем.
        return JSONResponse(status_code=200, content=_seen_deliveries[x_prodavan_delivery])

    # Полезная работа пилота: фиксируем факт доставки. Выходов в агентский
    # pod-gateway здесь нет — плагин работает только со своей БД и Gateway.
    result = {
        "ok": True,
        "plugin_id": PLUGIN_ID,
        "event": x_prodavan_event,
        "delivery": x_prodavan_delivery,
    }
    _seen_deliveries[x_prodavan_delivery] = result
    return JSONResponse(status_code=200, content=result)


@app.get("/api/greetings")
async def list_greetings(
    company_id: str,
    project_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Список приветствий своей компании (полный SQL, typed-колонки, §2)."""
    _authorized_gateway_call()
    pool = await _get_pool()
    safe_limit = max(1, min(limit, 200))
    # Разделение по company_id/project_id — бизнес-логика плагина, не RLS (§2.4).
    async with pool.acquire() as conn:
        if project_id is None:
            rows = await conn.fetch(
                "SELECT id, company_id, cabinet_id, project_id, message, created_at "
                "FROM greetings WHERE company_id = $1 ORDER BY created_at DESC LIMIT $2",
                company_id,
                safe_limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, company_id, cabinet_id, project_id, message, created_at "
                "FROM greetings WHERE company_id = $1 AND project_id = $2 "
                "ORDER BY created_at DESC LIMIT $3",
                company_id,
                project_id,
                safe_limit,
            )
    return [dict(row) for row in rows]


@app.post("/api/greetings")
async def create_greeting(payload: dict[str, Any]) -> Response:
    """Записать приветствие в СВОЮ БД и обновить счётчик в той же транзакции."""
    _authorized_gateway_call()
    company_id = payload.get("company_id")
    message = payload.get("message")
    if not isinstance(company_id, str) or not company_id:
        raise HTTPException(status_code=422, detail="company_id обязателен")
    if not isinstance(message, str) or not message.strip():
        raise HTTPException(status_code=422, detail="message обязателен")

    cabinet_id = payload.get("cabinet_id")
    project_id = payload.get("project_id")
    greeting_id = uuid.uuid4()
    pool = await _get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO greetings (id, company_id, cabinet_id, project_id, message) "
            "VALUES ($1, $2, $3, $4, $5)",
            greeting_id,
            company_id,
            cabinet_id,
            project_id,
            message,
        )
        # Одна строка статистики на компанию; UPSERT идемпотентен к повторам.
        await conn.execute(
            "INSERT INTO greeting_stats (id, greetings_count) "
            "VALUES ($1, 1) "
            "ON CONFLICT (id) DO UPDATE "
            "SET greetings_count = greeting_stats.greetings_count + 1",
            uuid.uuid5(uuid.NAMESPACE_OID, f"hello_plugin:stats:{company_id}"),
        )
    return JSONResponse(
        status_code=201,
        content={"id": str(greeting_id), "company_id": company_id, "message": message},
    )


@app.get("/api/stats")
async def stats(company_id: str) -> dict[str, Any]:
    """Агрегат из typed-таблицы ``greeting_stats`` (не jsonb)."""
    _authorized_gateway_call()
    pool = await _get_pool()
    stat_id = uuid.uuid5(uuid.NAMESPACE_OID, f"hello_plugin:stats:{company_id}")
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT greetings_count FROM greeting_stats WHERE id = $1", stat_id
        )
    return {"company_id": company_id, "greetings_count": int(count or 0)}
