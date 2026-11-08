"""HTTP surface плагинов ``/capabilities/v1/*`` (задача 04, F2).

**Отдельный route-group Plugin Gateway** (v4 §4.1, §7.2, §13.3, §14.3).
``:8001`` pod-gateway и его allowlist остаются агентам БЕЗ ИЗМЕНЕНИЙ: этот
роутер НЕ подключается к ``main_pod.py`` и не трогает ``core/middleware.py``.
Родитель монтирует его в Plugin Gateway при F11-интеграции::

    app.state.plugin_keyring = ...                 # identity.keys.KeyRing
    app.state.plugin_revocation = ...             # identity.revocation.GrantRevocation
    app.state.plugin_capability_service = ...     # capabilities.PluginCapabilityService
    app.include_router(plugin_capabilities.router)  # БЕЗ /api/v1 prefix!

Префикс обязателен ровно ``/capabilities/v1``: ``assert_plugin_surface``
(``identity/allowlist.py`` задачи 02) допускает только этот путь; любой другой
(в т.ч. ``/api/v1/...`` или ``/agent/events``) даёт 403 ``FORBIDDEN_SURFACE``.

Контракты:
  * все тела — типизированные Pydantic-модели из ``transport/bodies.py``,
    ``extra='forbid'``; поля ``sql``/``command``/``args``/``raw`` и любые
    identity-поля -> 422. Никакого generic row/action dispatch (ADR-002).
  * каждый эндпоинт ходит через ``PluginCapabilityService`` (задача 03);
    успех -> ``{decision, data}``, отказ -> ``application/problem+json`` с ``code``.
  * все эндпоинты, включая ``/health``, требуют проверенный ``PluginClaims``.
    Identity берётся ТОЛЬКО из JWT; входящие ``X-Plugin-*`` / ``X-Company-ID``
    игнорируются (§7.2: Gateway их перезаписывает, backend всё равно проверяет
    токен).

Fail-closed: отсутствие keyring/revocation/сервиса -> 503, а не «пропустить».
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ValidationError as PydanticValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from prodavan.application.plugins.transport.bodies import (
    CacheDeleteBody,
    CacheGetBody,
    CacheIncrBody,
    CachePublishBody,
    CacheSetBody,
    DecisionResponse,
    DocsQueryBody,
    EventsPublishBody,
    HealthResponse,
    JobsCancelBody,
    JobsGetBody,
    JobsSubmitBody,
    McpRegisterBody,
    NetworkRequestBody,
    ObjectsCredentialsBody,
    ObjectsDeleteBody,
    ObjectsListBody,
    PodsInvokeBody,
    SecretsReadBody,
)
from prodavan.application.plugins.transport.bridge import (
    CACHE_OPERATIONS,
    build_cache_request,
    get_capability_service,
    invoke_capability,
    problem_response,
)
from prodavan.application.plugins.transport.deps import PluginClaimsDep
from prodavan.application.plugins.transport.errors import (
    GENERIC_DISPATCH_FIELDS,
    PluginProblem,
    PluginTransportError,
    plugin_validation_problem,
    problem_from_exception,
)


class PluginRoute(APIRoute):
    """Маршрут с гарантированным ``problem+json`` для ВСЕХ ошибок.

    Роутер самодостаточен: родитель может смонтировать его в отдельном
    приложении Plugin Gateway, где не зарегистрированы глобальные
    ``exception_handlers`` из ``main.py``. Поэтому перехватываем ошибки на
    уровне route (включая ``RequestValidationError`` из тел/зависимостей) и
    всегда возвращаем типизированное тело с ``code``.
    """

    def get_route_handler(self):  # type: ignore[no-untyped-def]
        original = super().get_route_handler()

        async def handler(request: Request):  # type: ignore[no-untyped-def]
            try:
                return await original(request)
            except PluginTransportError as exc:
                return problem_response(exc.problem, request)
            except RequestValidationError as exc:
                return problem_response(plugin_validation_problem(exc), request)
            except StarletteHTTPException as exc:
                # 404/405 от роутера — тоже problem+json с кодом.
                detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
                code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
                return problem_response(
                    PluginProblem(exc.status_code, code, code.replace("_", " ").title(), detail),
                    request,
                )
            except Exception as exc:
                mapped = problem_from_exception(exc)
                if mapped is not None:
                    return problem_response(mapped, request)
                raise

        return handler


router = APIRouter(
    prefix="/capabilities/v1",
    tags=["plugin-capabilities"],
    route_class=PluginRoute,
)


async def _typed_payload(request: Request, model: type[BaseModel]) -> BaseModel:
    """Прочитать typed payload для GET/DELETE: JSON-тело ИЛИ query-параметры.

    По ТЗ каждое тело — типизированная Pydantic-модель. GET с JSON-телом
    нестандартен, поэтому поддерживаем оба канала, но валидируем ОДНОЙ и той
    же моделью с ``extra='forbid'``. Запрещённые поля (``sql``, ``command``,
    ``args``, ``raw``, ...) и попытка подменить identity отклоняются сразу.
    Никакого ``dict[str, Any]`` в публичный контракт не уходит.
    """
    raw_body = await request.body()
    if raw_body:
        try:
            raw: Any = json.loads(raw_body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise PluginTransportError(
                PluginProblem(422, "VALIDATION_ERROR", "Validation Error", "request body is not valid JSON")
            ) from exc
    else:
        raw = dict(request.query_params)

    if isinstance(raw, dict):
        from prodavan.application.plugins.transport.bodies import IDENTITY_FIELDS

        forbidden = GENERIC_DISPATCH_FIELDS | IDENTITY_FIELDS
        found = sorted(str(key).lower() for key in raw if str(key).lower() in forbidden)
        if found:
            raise PluginTransportError(
                PluginProblem(
                    422,
                    "FORBIDDEN_GENERIC_DISPATCH",
                    "Validation Error",
                    "generic dispatch fields are forbidden: " + ", ".join(found),
                )
            )
    try:
        return model.model_validate(raw)
    except PydanticValidationError as exc:
        # Не публикуем сырой input_value из pydantic-ошибки в ответе.
        raise PluginTransportError(
            PluginProblem(
                422,
                "VALIDATION_ERROR",
                "Validation Error",
                f"invalid {model.__name__}: request contains unknown or malformed fields",
            )
        ) from exc


def _run(
    *,
    request: Request,
    claims: Any,
    capability: str,
    operation: str,
    domain_request: Any,
) -> dict[str, Any]:
    """Единая точка вызова сервиса (без generic dispatch)."""
    service = get_capability_service(request)
    return invoke_capability(
        request=request,
        claims=claims,
        service=service,
        capability=capability,
        operation=operation,
        domain_request=domain_request,
    )


# ---------------------------------------------------------------------------
# cache: POST /cache/{op}
# ---------------------------------------------------------------------------

_CACHE_BODIES: dict[str, type[BaseModel]] = {
    "get": CacheGetBody,
    "set": CacheSetBody,
    "delete": CacheDeleteBody,
    "incr": CacheIncrBody,
    "publish": CachePublishBody,
}


@router.post("/cache/{op}", response_model=DecisionResponse)
async def cache_operation(
    op: str,
    body: CacheGetBody | CacheSetBody | CacheDeleteBody | CacheIncrBody | CachePublishBody,
    request: Request,
    claims: PluginClaimsDep,
) -> dict[str, Any]:
    """get/set/delete/incr/publish — закрытый перечень операций (§4.3).

    Тело — ОДНА из пяти типизированных моделей (union). Неизвестная операция
    или тело другой операции -> 422. Повторная валидация через ожидаемую
    модель нужна, потому что несколько моделей структурно совпадают (например,
    ``CacheGetBody`` и ``CacheDeleteBody`` содержат только ``key``).
    """
    from prodavan.application.plugins.transport import bridge as bridge_mod

    if op not in CACHE_OPERATIONS:
        raise PluginTransportError(
            PluginProblem(422, "VALIDATION_ERROR", "Validation Error", f"unknown cache operation: {op!r}")
        )
    expected = _CACHE_BODIES[op]
    try:
        typed = expected.model_validate(body.model_dump())
    except PydanticValidationError as exc:
        raise PluginTransportError(
            PluginProblem(
                422,
                "VALIDATION_ERROR",
                "Validation Error",
                f"cache/{op} requires {expected.__name__}",
            )
        ) from exc

    domain_request = bridge_mod.build_cache_request(op, typed, claims)
    return _run(
        request=request,
        claims=claims,
        capability="cache",
        operation=f"cache.{op}",
        domain_request=domain_request,
    )


# ---------------------------------------------------------------------------
# objects
# ---------------------------------------------------------------------------


@router.post("/objects/credentials", response_model=DecisionResponse)
async def objects_credentials(
    body: ObjectsCredentialsBody,
    request: Request,
    claims: PluginClaimsDep,
) -> dict[str, Any]:
    """Short-lived scoped credentials / presigned (§4.3, §5.4)."""
    from prodavan.application.plugins.transport import bridge as bridge_mod

    return _run(
        request=request,
        claims=claims,
        capability="objects",
        operation="objects.scoped_credentials",
        domain_request=bridge_mod._objects_credentials(body, claims),
    )


@router.get("/objects", response_model=DecisionResponse)
async def objects_get(
    request: Request,
    claims: PluginClaimsDep,
) -> dict[str, Any]:
    """``GET /objects`` — типизированный список (тело ИЛИ query, без raw dict)."""
    from prodavan.application.plugins.transport import bridge as bridge_mod

    typed = await _typed_payload(request, ObjectsListBody)
    return _run(
        request=request,
        claims=claims,
        capability="objects",
        operation="objects.list",
        domain_request=bridge_mod._objects_list(typed, claims),
    )


@router.post("/objects", response_model=DecisionResponse)
async def objects_list(
    body: ObjectsListBody,
    request: Request,
    claims: PluginClaimsDep,
) -> dict[str, Any]:
    """Тот же typed контракт, что и ``GET /objects`` (для POST-клиентов)."""
    from prodavan.application.plugins.transport import bridge as bridge_mod

    return _run(
        request=request,
        claims=claims,
        capability="objects",
        operation="objects.list",
        domain_request=bridge_mod._objects_list(body, claims),
    )


@router.delete("/objects/{key}", response_model=DecisionResponse)
async def objects_delete(
    key: str,
    request: Request,
    claims: PluginClaimsDep,
) -> dict[str, Any]:
    from prodavan.application.plugins.transport import bridge as bridge_mod

    typed = await _typed_payload(request, ObjectsDeleteBody)
    try:
        domain_request = bridge_mod._objects_delete(key, typed, claims)
    except ValueError as exc:
        raise PluginTransportError(
            PluginProblem(422, "VALIDATION_ERROR", "Validation Error", str(exc))
        ) from exc
    return _run(
        request=request,
        claims=claims,
        capability="objects",
        operation="objects.delete",
        domain_request=domain_request,
    )


# ---------------------------------------------------------------------------
# docs / events / jobs
# ---------------------------------------------------------------------------


@router.post("/docs/query", response_model=DecisionResponse)
async def docs_query(
    body: DocsQueryBody,
    request: Request,
    claims: PluginClaimsDep,
) -> dict[str, Any]:
    """Typed запрос: предикаты из закрытого набора операторов, не Mongo-JSON."""
    from prodavan.application.plugins.transport import bridge as bridge_mod

    try:
        domain_request = bridge_mod._docs_query(body, claims)
    except (ValueError, KeyError) as exc:
        raise PluginTransportError(
            PluginProblem(422, "VALIDATION_ERROR", "Validation Error", "invalid typed docs query")
        ) from exc
    return _run(
        request=request,
        claims=claims,
        capability="docs",
        operation="docs.query",
        domain_request=domain_request,
    )


@router.post("/events/publish", response_model=DecisionResponse)
async def events_publish(
    body: EventsPublishBody,
    request: Request,
    claims: PluginClaimsDep,
) -> dict[str, Any]:
    from prodavan.application.plugins.transport import bridge as bridge_mod

    return _run(
        request=request,
        claims=claims,
        capability="events",
        operation="events.publish",
        domain_request=bridge_mod._events_publish(body, claims),
    )


@router.post("/jobs/submit", response_model=DecisionResponse)
async def jobs_submit(
    body: JobsSubmitBody,
    request: Request,
    claims: PluginClaimsDep,
) -> dict[str, Any]:
    from prodavan.application.plugins.transport import bridge as bridge_mod

    return _run(
        request=request,
        claims=claims,
        capability="jobs",
        operation="jobs.submit",
        domain_request=bridge_mod._jobs_submit(body, claims),
    )


@router.get("/jobs/{job_id}", response_model=DecisionResponse)
async def jobs_get(
    job_id: str,
    request: Request,
    claims: PluginClaimsDep,
) -> dict[str, Any]:
    """``run_id`` берётся из типизированного payload, не из произвольного query."""
    from prodavan.application.plugins.transport import bridge as bridge_mod

    typed = await _typed_payload(request, JobsGetBody)
    return _run(
        request=request,
        claims=claims,
        capability="jobs",
        operation="jobs.get",
        domain_request=bridge_mod._jobs_get(job_id, typed, claims),
    )


@router.post("/jobs/{job_id}/cancel", response_model=DecisionResponse)
async def jobs_cancel(
    job_id: str,
    body: JobsCancelBody,
    request: Request,
    claims: PluginClaimsDep,
) -> dict[str, Any]:
    from prodavan.application.plugins.transport import bridge as bridge_mod

    return _run(
        request=request,
        claims=claims,
        capability="jobs",
        operation="jobs.cancel",
        domain_request=bridge_mod._jobs_cancel(job_id, body, claims),
    )


# ---------------------------------------------------------------------------
# pods / mcp / secrets / network
# ---------------------------------------------------------------------------


@router.post("/pods/invoke", response_model=DecisionResponse)
async def pods_invoke(
    body: PodsInvokeBody,
    request: Request,
    claims: PluginClaimsDep,
) -> dict[str, Any]:
    """Typed action из закрытого перечня; ``command``/``args`` запрещены."""
    from prodavan.application.plugins.transport import bridge as bridge_mod

    try:
        domain_request = bridge_mod._pods_invoke(body, claims)
    except ValueError as exc:
        raise PluginTransportError(
            PluginProblem(422, "VALIDATION_ERROR", "Validation Error", "invalid pods.invoke contract")
        ) from exc
    return _run(
        request=request,
        claims=claims,
        capability="pods",
        operation="pods.invoke",
        domain_request=domain_request,
    )


@router.post("/mcp/register", response_model=DecisionResponse)
async def mcp_register(
    body: McpRegisterBody,
    request: Request,
    claims: PluginClaimsDep,
) -> dict[str, Any]:
    from prodavan.application.plugins.transport import bridge as bridge_mod

    try:
        domain_request = bridge_mod._mcp_register(body, claims)
    except (ValueError, KeyError) as exc:
        raise PluginTransportError(
            PluginProblem(422, "VALIDATION_ERROR", "Validation Error", "invalid mcp.register contract")
        ) from exc
    return _run(
        request=request,
        claims=claims,
        capability="mcp",
        operation="mcp.register",
        domain_request=domain_request,
    )


@router.post("/secrets/read", response_model=DecisionResponse)
async def secrets_read(
    body: SecretsReadBody,
    request: Request,
    claims: PluginClaimsDep,
) -> dict[str, Any]:
    """Возвращает ссылку/версию; plaintext через HTTP-контракт не уходит."""
    from prodavan.application.plugins.transport import bridge as bridge_mod

    return _run(
        request=request,
        claims=claims,
        capability="secrets",
        operation="secrets.read",
        domain_request=bridge_mod._secrets_read(body, claims),
    )


@router.post("/network/request", response_model=DecisionResponse)
async def network_request(
    body: NetworkRequestBody,
    request: Request,
    claims: PluginClaimsDep,
) -> dict[str, Any]:
    """Egress: политика разрешается из grant'а в БД, НЕ из JWT (кр. 22)."""
    from prodavan.application.plugins.transport import bridge as bridge_mod

    try:
        domain_request = bridge_mod._network_request(body, claims)
    except ValueError as exc:
        raise PluginTransportError(
            PluginProblem(422, "VALIDATION_ERROR", "Validation Error", "invalid network.request contract")
        ) from exc
    return _run(
        request=request,
        claims=claims,
        capability="network",
        operation="network.egress",
        domain_request=domain_request,
    )


# ---------------------------------------------------------------------------
# health (защищённый, без вызова адаптеров)
# ---------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse)
async def plugin_health(claims: PluginClaimsDep) -> dict[str, Any]:
    """Liveness capability-surface: требует валидный plugin-токен.

    Для k8s-проб у родителя есть корневой ``/health`` (без авторизации);
    этот эндпоинт проверяет именно plugin trust-контур (кр. 17) и является
    удобной проверкой, что ``company_id`` взят из claims, а не из заголовка.
    """
    return {"status": "ok", "surface": "plugin-capabilities", "company_id": claims.company_id}


# ---------------------------------------------------------------------------
# Явный 404 для незарегистрированных capability-путей. Регистрируется ПОСЛЕ
# всех конкретных маршрутов: Starlette матчит по порядку, поэтому конкретные
# эндпоинты всегда имеют приоритет. Fail-closed: неизвестный путь = отказ.
# ---------------------------------------------------------------------------


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def _unknown_path(path: str, claims: PluginClaimsDep) -> None:
    """404 для неизвестного capability-пути, но ТОЛЬКО после проверки claims.

    ``PluginClaimsDep`` обязателен и здесь: если плагин с capability-токеном
    стучится на ``/agent/events`` (или любой не-capabilities путь), зависимость
    вызывает ``assert_plugin_surface`` и даёт 403 ``FORBIDDEN_SURFACE`` ещё до
    404. Так роутер сам соблюдает критерий №17 даже без родительского
    middleware (защита в глубину, fail-closed).
    """
    raise PluginTransportError(
        PluginProblem(404, "NOT_FOUND", "Not Found", f"unknown plugin capability path: /{path}")
    )


async def plugin_validation_handler(request: Request, exc: Exception) -> JSONResponse:
    """Опциональный app-level handler для родителя (PluginRoute уже закрывает)."""
    if isinstance(exc, RequestValidationError):
        return problem_response(plugin_validation_problem(exc), request)
    mapped = problem_from_exception(exc)
    if mapped is not None:
        return problem_response(mapped, request)
    return problem_response(
        PluginProblem(500, "INTERNAL_ERROR", "Internal Error", "unexpected plugin surface error"),
        request,
    )


__all__ = [
    "PluginRoute",
    "plugin_validation_handler",
    "router",
]
