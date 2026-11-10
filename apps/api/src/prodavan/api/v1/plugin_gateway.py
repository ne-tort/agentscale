"""FastAPI ``APIRouter`` Plugin Gateway (F6, §7.1/§7.2/§7.5).

Единая точка входа для::

    POST/GET/PUT/DELETE /webhook/{plugin}/{company}/{path:path}
    GET/HEAD/POST      /pluginpage/{plugin}/{company}/{path:path}
    GET                /health

Роутер **не владеет** композицией портов: интегратор (родитель, F11) вызывает
``install_plugin_gateway_runtime(app, runtime)`` и подключает ``router`` в
``main.create_app`` с БЕЗ префикса ``/api/v1`` — внешние адреса из §7.1
живут в корне хоста, а Ingress с ``router.priority: 120`` направляет их сюда
(задача 15). До монтирования рантайма все запросы дают явный ``503``, а не
тихое «ничего не найдено» (fail-closed).

КАЖДЫЙ обработчик проходит через ``GatewayAuth`` + ``GatewayProxy``:

* ``/webhook/*`` — HMAC + идемпотентность + rate limit, затем прокси в backend;
  повтор ``X-Prodavan-Delivery`` → ``200`` БЕЗ повторной обработки (без forward);
* ``/pluginpage/.../api/*`` → backend (обязательная сессия для анонима = 401);
* ``/pluginpage/.../static`` → отдельный static-origin, НЕ index.html;
* ``/webhook/`` НИКОГДА не отдаёт ``index.html`` и не уезжает в SPA (§11 №9).

Backend **обязан** проверить capability-токен/JWT, а не доверять шлюзу «по
умолчанию» (§7.2) — шлюз лишь перезаписывает доверенные заголовки.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.requests import ClientDisconnect

from prodavan.application.plugins.gateway.auth import (
    GatewayAuth,
    PageAuthResult,
    WebhookAuthResult,
)
from prodavan.application.plugins.gateway.canonical import canonical_raw_query
from prodavan.application.plugins.gateway.errors import (
    BackendNotReadyError,
    GatewayError,
    GatewayErrorCode,
)
from prodavan.application.plugins.gateway.ports import BackendResponse
from prodavan.application.plugins.gateway.proxy import GatewayProxy

router = APIRouter(tags=["plugin-gateway"])

#: Ключ рантайма в ``app.state``. Монтируется интегратором, не создаётся здесь.
RUNTIME_STATE_ATTR = "plugin_gateway_runtime"


# ---------------------------------------------------------------------------
# Композиция (fail-closed)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PluginGatewayRuntime:
    """Связка «решение + исполнение». Все порты приходят извне."""

    auth: GatewayAuth
    proxy: GatewayProxy

    def __post_init__(self) -> None:
        if self.auth is None or self.proxy is None:
            raise GatewayError(
                GatewayErrorCode.RUNTIME_NOT_INSTALLED,
                detail="PluginGatewayRuntime requires both auth and proxy",
            )


def install_plugin_gateway_runtime(app: Any, runtime: PluginGatewayRuntime) -> None:
    """Composition hook для родителя: смонтировать рантайм на ``app.state``."""
    setattr(app.state, RUNTIME_STATE_ATTR, runtime)
    # install_api_plugin_platform attaches the fully built platform immediately
    # before this hook. Capture its concrete ports once and install an explicit
    # per-app dependency; request handlers never perform a global state lookup.
    platform = getattr(app.state, "plugin_platform", None)
    registry = getattr(platform, "registry", None)
    if callable(getattr(registry, "resolve", None)):
        from prodavan.application.plugins.runtime.provider_api import ProviderError
        from prodavan.application.plugins.runtime.providers import (
            build_scoped_runtime_provider,
            install_scoped_runtime_provider,
        )

        try:
            provider = build_scoped_runtime_provider(platform)
        except ProviderError:
            # Custom/incomplete compositions keep the zero-arg 503 dependency;
            # they must not break unrelated webhook/pluginpage gateway routes.
            provider = None
        if provider is not None:
            install_scoped_runtime_provider(app, provider)


def get_plugin_gateway_runtime(request: Request) -> PluginGatewayRuntime | None:
    return getattr(request.app.state, RUNTIME_STATE_ATTR, None)


def _require_runtime(request: Request) -> PluginGatewayRuntime:
    runtime = get_plugin_gateway_runtime(request)
    if runtime is None:
        raise GatewayError(
            GatewayErrorCode.RUNTIME_NOT_INSTALLED,
            detail="plugin gateway runtime is not wired (fail-closed)",
        )
    return runtime


# ---------------------------------------------------------------------------
# Вспомогательное: сборка ответа upstream без буферизации SSE
# ---------------------------------------------------------------------------


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "trace_id", "") or "")


def _incoming_headers(request: Request) -> dict[str, str]:
    return dict(request.headers.items())


async def _read_bounded_body(request: Request, *, limit_bytes: int) -> bytes:
    """Прочитать не более configured cap без лишнего chunk после его достижения.

    Content-Length проверяется до первого чтения. ASGI ``more_body`` позволяет
    отличить валидное тело ровно с cap в последнем сообщении от продолжения,
    которое отклоняется сразу, до чтения следующего chunk. request.body() не
    вызывается.
    """
    if limit_bytes < 1:
        raise GatewayError(
            GatewayErrorCode.PORT_UNAVAILABLE,
            detail="request body limit must be positive",
        )
    content_length = request.headers.get("content-length")
    if content_length is not None:
        if not content_length.isascii() or not content_length.isdigit():
            raise GatewayError(
                GatewayErrorCode.INVALID_CONTENT_LENGTH,
                detail="Content-Length must be a non-negative decimal integer",
            )
        try:
            declared = int(content_length, 10)
        except (TypeError, ValueError) as exc:
            raise GatewayError(
                GatewayErrorCode.INVALID_CONTENT_LENGTH,
                detail="Content-Length must be a non-negative decimal integer",
            ) from exc
        if declared < 0:
            raise GatewayError(
                GatewayErrorCode.INVALID_CONTENT_LENGTH,
                detail="Content-Length must be a non-negative decimal integer",
            )
        if declared > limit_bytes:
            raise GatewayError(
                GatewayErrorCode.PAYLOAD_TOO_LARGE,
                detail=f"request body exceeds the configured {limit_bytes} byte limit",
            )

    body = bytearray()
    while True:
        message = await request.receive()
        message_type = message.get("type")
        if message_type == "http.disconnect":
            raise ClientDisconnect()
        if message_type != "http.request":
            continue
        chunk = message.get("body", b"")
        more_body = bool(message.get("more_body", False))
        if chunk:
            remaining = limit_bytes - len(body)
            if len(chunk) > remaining:
                # Не добавляем лишние байты и не читаем следующий chunk.
                raise GatewayError(
                    GatewayErrorCode.PAYLOAD_TOO_LARGE,
                    detail=f"request body exceeds the configured {limit_bytes} byte limit",
                )
            body.extend(chunk)
        if len(body) == limit_bytes and more_body:
            # Cap уже достигнут, а ASGI подтверждает продолжение: 413 до auth и
            # до получения следующего chunk из producer.
            raise GatewayError(
                GatewayErrorCode.PAYLOAD_TOO_LARGE,
                detail=f"request body exceeds the configured {limit_bytes} byte limit",
            )
        if not more_body:
            return bytes(body)


def _raw_query(request: Request) -> bytes:
    value = request.scope.get("query_string", b"")
    try:
        return canonical_raw_query(value)
    except (TypeError, ValueError) as exc:
        raise GatewayError(
            GatewayErrorCode.INVALID_PATH,
            detail="raw query must use printable ASCII/percent-encoding without fragments",
        ) from exc


def _is_trusted_proxy(host: str, *, cidrs: tuple[str, ...]) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    for configured in cidrs:
        try:
            network = ipaddress.ip_network(configured, strict=False)
        except ValueError:
            continue
        if address in network:
            return True
    return False


def _client_ip(request: Request) -> str:
    """Peer IP; XFF только от настроенного trusted proxy, иначе игнорируется."""
    direct = str(getattr(request.client, "host", "") or "unknown")
    limits = _require_runtime(request).auth.limits
    if not _is_trusted_proxy(direct, cidrs=limits.trusted_proxy_cidrs):
        return direct
    forwarded = request.headers.get("x-forwarded-for", "")
    candidates: list[str] = []
    for item in forwarded.split(","):
        candidate = item.strip()
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        candidates.append(candidate)
    # Справа налево пропускаем доверенные proxy hops. Первый untrusted — ближайший
    # клиентский адрес, которому можно доверять в рамках этой proxy-цепочки.
    for candidate in reversed(candidates):
        if not _is_trusted_proxy(candidate, cidrs=limits.trusted_proxy_cidrs):
            return candidate
    return candidates[0] if candidates else direct


def _is_sse(headers: dict[str, str]) -> bool:
    """Потоковый ответ нужен для SSE (Accept / Content-Type)."""
    lowered = {k.lower(): v for k, v in headers.items() if isinstance(k, str)}
    accept = lowered.get("accept", "")
    content_type = lowered.get("content-type", "")
    return "text/event-stream" in (accept + " " + content_type).lower()


def _to_response(
    response: BackendResponse, *, stream: bool, proxy: GatewayProxy
) -> Response:
    """Преобразовать ``BackendResponse`` в FastAPI-ответ.

    Если ``stream=True`` — отдаём ``StreamingResponse`` с ЖИВЫМ итератором
    чанков: тело нигде не собирается в ``bytes`` (SSE без буферизации, §7.2).
    """
    out_headers = proxy.response_headers(response, stream=stream)
    if stream or response.is_streaming:
        lowered = {k.lower(): v for k, v in response.headers.items() if isinstance(k, str)}
        media_type = lowered.get("content-type") or "text/event-stream"
        try:
            chunks = proxy.streaming_chunks(response)
        except GatewayError as exc:
            return _problem(exc)
        return StreamingResponse(
            chunks,
            status_code=response.status_code,
            headers=out_headers,
            media_type=media_type,
        )
    return Response(
        content=response.body or b"",
        status_code=response.status_code,
        headers=out_headers,
    )


def _backend_not_ready_response(
    runtime: PluginGatewayRuntime | None,
) -> JSONResponse:
    """``202 + Retry-After`` при «backend ещё спит/не Ready» (§7.4)."""
    retry_after = (
        runtime.auth.limits.retry_after_seconds if runtime is not None else 3
    )
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "code": "PLUGIN_BACKEND_WAKING",
            "detail": "plugin backend is waking up; retry after the indicated delay",
        },
        headers={"Retry-After": str(retry_after)},
    )


def _error_headers(exc: GatewayError) -> dict[str, str]:
    headers: dict[str, str] = {}
    if exc.retry_after_seconds is not None:
        headers["Retry-After"] = str(exc.retry_after_seconds)
    return headers


def _problem(exc: GatewayError) -> JSONResponse:
    """RFC 7807 problem+json, совместимый с ``api/exception_handlers.py``.

    Роутер поднимает ``GatewayError`` (наследник ``AppError``), поэтому в
    рабочем приложении ответ построит общий обработчик. Явный ``_problem``
    нужен для ``202``/``Retry-After`` и для юнит-тестов, монтирующих только
    этот ``router`` без ``register_exception_handlers``.
    """
    return JSONResponse(
        status_code=exc.status,
        content={
            "type": f"https://prodavan.dev/errors/{exc.code}",
            "title": exc.title,
            "status": exc.status,
            "code": exc.code,
            "detail": exc.detail,
        },
        headers=_error_headers(exc),
        media_type="application/problem+json",
    )


# ---------------------------------------------------------------------------
# /health (liveness всегда-running шлюза; НЕ readiness спящего backend)
# ---------------------------------------------------------------------------


@router.get("/health")
async def health(request: Request) -> Response:
    """Liveness шлюза. Не проверяет спящие backend'ы (они могут быть 0 реплик)."""
    try:
        _require_runtime(request)
    except GatewayError as exc:
        return _problem(exc)
    return JSONResponse(status_code=200, content={"status": "ok", "service": "plugin-gateway"})


# ---------------------------------------------------------------------------
# Явные «голые» префиксы: критерий §11 №9.
#
# Без этих маршрутов ``/webhook/`` (без plugin/company) и ``/pluginpage/``
# уехали бы в SPA catch-all Ingress'а и отдали бы index.html — ровно тот
# инцидент «login POST → index.html», который уже чинили в #331 (§7.1).
# Здесь это ЯВНЫЙ отказ шлюза, а не статика.
# ---------------------------------------------------------------------------


def _no_spa_fallback() -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "type": "https://prodavan.dev/errors/PLUGIN_GATEWAY_INVALID_PATH",
            "title": "Invalid plugin path",
            "status": 400,
            "code": "PLUGIN_GATEWAY_INVALID_PATH",
            "detail": "plugin and company path segments are required; SPA fallback is forbidden",
        },
        media_type="application/problem+json",
    )


@router.api_route(
    "/webhook",
    methods=["GET", "POST", "PUT", "DELETE"],
    include_in_schema=False,
)
async def webhook_bare() -> Response:
    return _no_spa_fallback()


@router.api_route(
    "/webhook/",
    methods=["GET", "POST", "PUT", "DELETE"],
    include_in_schema=False,
)
async def webhook_bare_slash() -> Response:
    return _no_spa_fallback()


@router.api_route(
    "/pluginpage",
    methods=["GET", "POST"],
    include_in_schema=False,
)
async def pluginpage_bare() -> Response:
    return _no_spa_fallback()


@router.api_route(
    "/pluginpage/{plugin}/{company}",
    methods=["GET", "POST"],
    include_in_schema=False,
)
async def pluginpage_bare_with_ids(plugin: str, company: str) -> Response:
    """Пустой ``path`` (``/pluginpage/p/c/``) — НЕ index.html (критерий №9)."""
    return _no_spa_fallback()


@router.api_route(
    "/webhook/{plugin}/{company}",
    methods=["GET", "POST", "PUT", "DELETE"],
    include_in_schema=False,
)
async def webhook_bare_with_ids(plugin: str, company: str) -> Response:
    """Пустой ``path`` у webhook — тоже явный отказ, а не SPA."""
    return _no_spa_fallback()


@router.api_route(
    "/webhook/{plugin}/{company}/",
    methods=["GET", "POST", "PUT", "DELETE"],
    include_in_schema=False,
)
async def webhook_bare_with_ids_slash(plugin: str, company: str) -> Response:
    """То же с завершающим ``/``: index.html не отдаётся ни в каком виде."""
    return _no_spa_fallback()


@router.api_route(
    "/pluginpage/{plugin}/{company}/",
    methods=["GET", "POST"],
    include_in_schema=False,
)
async def pluginpage_bare_with_ids_slash(plugin: str, company: str) -> Response:
    """Пустой статический путь с ``/`` — НЕ index.html."""
    return _no_spa_fallback()


@router.api_route(
    "/pluginpage/",
    methods=["GET", "POST"],
    include_in_schema=False,
)
async def pluginpage_bare_slash() -> Response:
    return _no_spa_fallback()


# ---------------------------------------------------------------------------
# /webhook/{plugin}/{company}/{path:path}
# ---------------------------------------------------------------------------


async def _handle_webhook(
    request: Request,
    *,
    plugin: str,
    company: str,
    path: str,
) -> Response:
    runtime: PluginGatewayRuntime | None = None
    try:
        runtime = _require_runtime(request)
        headers = _incoming_headers(request)
        raw_query = _raw_query(request)
        # HMAC считается по фактически прочитанным raw bytes; cap срабатывает
        # до auth и не даёт chunked-клиенту обойти проверку Content-Length.
        raw_body = await _read_bounded_body(
            request, limit_bytes=runtime.auth.limits.webhook_body_limit_bytes
        )
        result: WebhookAuthResult = await runtime.auth.authorize_webhook(
            plugin_id=plugin,
            company_id=company,
            path=path,
            method=request.method,
            raw_body=raw_body,
            headers=headers,
            request_id=_request_id(request),
            raw_query=raw_query,
            client_ip=_client_ip(request),
        )

        # Повтор delivery → 200 БЕЗ повторной обработки (§7.5, критерий №7).
        # Ни forward, ни wake, ни повторной записи в outbox не происходит.
        if result.duplicate:
            return JSONResponse(
                status_code=200,
                content={
                    "status": "duplicate",
                    "delivery_id": result.delivery_id,
                    "detail": "delivery already processed; no reprocessing",
                },
                headers={"Idempotent-Replayed": "true"},
            )

        try:
            response = await runtime.proxy.forward(
                path=path,
                method=request.method,
                body=raw_body,
                headers=headers,
                claims=result.claims,
                grant=result.grant,
                stream=False,
                request_id=_request_id(request),
                query_string=raw_query,
                verified_webhook=result.verified,
            )
        except Exception:
            # Timeout/5xx transport/not-ready не считаются обработкой. Release
            # делаем best-effort, чтобы не маскировать исходный typed отказ;
            # при недоступном Redis lease сам истечёт по короткому TTL.
            try:
                await runtime.auth.release_webhook(result)
            except GatewayError:
                pass
            raise

        if 200 <= response.status_code < 300 and response.status_code != 202:
            # 202 означает лишь принятие backend'ом, но НЕ подтверждённую
            # обработку; только остальные 2xx создают durable idempotency done.
            await runtime.auth.commit_webhook(result)
        else:
            await runtime.auth.release_webhook(result)
        return _to_response(response, stream=False, proxy=runtime.proxy)
    except BackendNotReadyError:
        return _backend_not_ready_response(runtime)
    except GatewayError as exc:
        return _problem(exc)


@router.post("/webhook/{plugin}/{company}/{path:path}")
async def webhook_post(request: Request, plugin: str, company: str, path: str) -> Response:
    return await _handle_webhook(request, plugin=plugin, company=company, path=path)


@router.get("/webhook/{plugin}/{company}/{path:path}")
async def webhook_get(request: Request, plugin: str, company: str, path: str) -> Response:
    """GET тоже проверяет подпись и НЕ отдаёт index.html (§11 №9)."""
    return await _handle_webhook(request, plugin=plugin, company=company, path=path)


@router.put("/webhook/{plugin}/{company}/{path:path}")
async def webhook_put(request: Request, plugin: str, company: str, path: str) -> Response:
    return await _handle_webhook(request, plugin=plugin, company=company, path=path)


@router.delete("/webhook/{plugin}/{company}/{path:path}")
async def webhook_delete(request: Request, plugin: str, company: str, path: str) -> Response:
    return await _handle_webhook(request, plugin=plugin, company=company, path=path)


# ---------------------------------------------------------------------------
# /pluginpage/{plugin}/{company}/{path:path}
# ---------------------------------------------------------------------------


def _is_api_path(path: str) -> bool:
    """``/api/*`` → backend; всё остальное → static-origin (§7.1).

    Сравнение по первому сегменту, регистронезависимо; ``/api`` (без хвоста)
    тоже считается backend-путем, чтобы исключение public_pages не открыло
    анонимный доступ к корню API.
    """
    first = path.strip("/").split("/", 1)[0].lower()
    return first == "api"


async def _handle_pluginpage(
    request: Request,
    *,
    plugin: str,
    company: str,
    path: str,
) -> Response:
    runtime: PluginGatewayRuntime | None = None
    try:
        runtime = _require_runtime(request)
        headers = _incoming_headers(request)
        raw_query = _raw_query(request)
        is_api = _is_api_path(path)
        body_limit = (
            runtime.auth.limits.typed_payload_limit_bytes
            if is_api
            else runtime.auth.limits.page_body_limit_bytes
        )
        body = await _read_bounded_body(request, limit_bytes=body_limit)
        stream = _is_sse(headers)
        result: PageAuthResult = await runtime.auth.authorize_page(
            plugin_id=plugin,
            company_id=company,
            path=path,
            headers=headers,
            is_api=is_api,
            method=request.method,
            raw_body=body,
            raw_query=raw_query,
            # Server-derived suffix uses the same namespace as signed public_pages.
            # Client headers/query cannot select a different authorization route.
            route_path="/" + path.lstrip("/"),
            request_id=_request_id(request),
            client_ip=_client_ip(request),
        )

        if is_api:
            # Backend: wake → hold-until-Ready → forward. Anonymous reaches this
            # branch only for an exact signed public read-only GET/HEAD declaration.
            response = await runtime.proxy.forward(
                path=path,
                method=request.method,
                body=body,
                headers=headers,
                claims=result.claims,
                grant=result.grant,
                stream=stream,
                request_id=_request_id(request),
                query_string=raw_query,
            )
        else:
            # Статика: отдельный always-running origin, без wake, без
            # capability-токена, без index.html-fallback. Reduced anonymous grant
            # already carries the exact authenticated/published digest binding.
            response = await runtime.proxy.forward_static(
                path=path,
                method=request.method,
                headers=headers,
                claims=result.claims,
                grant=result.grant,
                is_api=is_api,
                request_id=_request_id(request),
                query_string=raw_query,
            )
        return _to_response(response, stream=stream, proxy=runtime.proxy)
    except BackendNotReadyError:
        return _backend_not_ready_response(runtime)
    except GatewayError as exc:
        return _problem(exc)


@router.get("/pluginpage/{plugin}/{company}/{path:path}")
async def pluginpage_get(request: Request, plugin: str, company: str, path: str) -> Response:
    return await _handle_pluginpage(request, plugin=plugin, company=company, path=path)


@router.head("/pluginpage/{plugin}/{company}/{path:path}")
async def pluginpage_head(request: Request, plugin: str, company: str, path: str) -> Response:
    """HEAD использует тот же read-only auth/proxy contract, что и GET."""
    return await _handle_pluginpage(request, plugin=plugin, company=company, path=path)


@router.post("/pluginpage/{plugin}/{company}/{path:path}")
async def pluginpage_post(request: Request, plugin: str, company: str, path: str) -> Response:
    """POST допустим только для ``/api/*``; статику POST не принимает.

    Авторизация одинакова для GET и POST: ``GatewayAuth`` + ``GatewayProxy``.
    """
    return await _handle_pluginpage(request, plugin=plugin, company=company, path=path)


# The protected provider surface shares this already-mounted router.  Keeping
# registration here avoids a second composition root or a main.py change.
from prodavan.api.v1.plugin_runtime import (
    install_plugin_runtime_routes as _install_plugin_runtime_routes,
)

_install_plugin_runtime_routes(router)
del _install_plugin_runtime_routes


__all__ = [
    "RUNTIME_STATE_ATTR",
    "PluginGatewayRuntime",
    "get_plugin_gateway_runtime",
    "install_plugin_gateway_runtime",
    "router",
]
