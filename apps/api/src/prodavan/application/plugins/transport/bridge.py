"""Мост HTTP -> доменные контракты и ``PluginCapabilityService`` (задача 04).

Зачем отдельный модуль:
  * ``bodies.py`` — Pydantic-тела (транспорт);
  * ``capabilities/contracts.py`` (задача 03) — frozen ``dataclass`` (домен);
  * здесь — единственная точка преобразования и вызова сервиса.

Решения:
  * **Ленивые импорты** ``capabilities.*``: задача 03 ещё может править свой
    каталог, и роутер не должен падать на ``import`` из-за чужого файла.
    Fail-closed при отсутствии интеграции реализован через 503, а не через
    ошибку импорта на старте приложения.
  * ``operation`` передаётся в ТОЧЕЧНОЙ форме (``cache.get``) — это публичный
    реестр ``OPERATION_TO_CAPABILITY`` из §4.4 и канонический формат, который
    ``PluginCapabilityService._prepare`` принимает, нормализуя внутри до
    ``get`` для ``_CoreScope``/``dispatch`` (согласовано с родителем).
  * ``CapabilityEnvelope`` собирается ТОЛЬКО из проверенных ``PluginClaims``.
    Задача 03 всё равно пересобирает envelope серверными значениями — это
    правильная защита от подмены через тело; здесь дублируем её на своей
    границе, чтобы в ``request`` не было клиентской identity.
  * Результат сервиса — ``PluginGatewayDecision`` с ``.decision``/``.code``/
    ``.data`` (deny НЕ exception). Маппинг в HTTP делается по ``code`` в первую
    очередь (429/403/503/422/401), исключения — только fallback.
  * Успех -> ``{decision: "allow", data: {...}}``; ``data`` — безопасная
    проекция типизированного ``TypedResponse`` (dataclass -> dict), без raw
    данных/токенов/PII.
"""

from __future__ import annotations

import dataclasses
from enum import Enum
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from fastapi import Depends, Request

from prodavan.application.plugins.identity.types import PluginClaims
from prodavan.application.plugins.transport.errors import (
    PluginProblem,
    PluginTransportError,
    plugin_problem_response,
    problem_from_decision,
    problem_from_exception,
)


@runtime_checkable
class CapabilityServiceLike(Protocol):
    """Структурный контракт ``PluginCapabilityService.invoke`` (задача 03)."""

    def invoke(
        self,
        *,
        claims: Any,
        capability: str,
        operation: str,
        request: Any,
    ) -> Any: ...


def get_capability_service(request: Request) -> CapabilityServiceLike:
    """Достать сервис из ``app.state`` (родитель связывает при F11).

    Варианты привязки (в порядке приоритета):
      * ``app.state.plugin_capability_service`` — готовый инстанс;
      * ``app.state.plugin_capability_service_factory`` — callable без аргументов;
      * ``app.state.plugin_capability_service_factory(request)`` — callable с
        запросом (если фабрике нужен ``Request``).

    Ничего не найдено → 503 ``SERVICE_UNAVAILABLE`` (fail-closed). Никаких
    «создай сервис с дефолтными адаптерами»: отсутствие интеграции = явная
    ошибка, а не молчаливый успех.
    """
    state = request.app.state
    service = getattr(state, "plugin_capability_service", None)
    if service is not None:
        return service  # type: ignore[return-value]

    factory = getattr(state, "plugin_capability_service_factory", None)
    if callable(factory):
        try:
            built = factory()
        except TypeError:
            try:
                built = factory(request)
            except Exception as exc:
                raise _service_unavailable(f"capability service factory failed: {exc}") from exc
        except Exception as exc:
            raise _service_unavailable(f"capability service factory failed: {exc}") from exc
        if built is None:
            raise _service_unavailable("capability service factory returned None")
        return built  # type: ignore[return-value]

    raise _service_unavailable(
        "PluginCapabilityService is not configured on app.state; plugin surface is closed"
    )


def _service_unavailable(detail: str) -> PluginTransportError:
    return PluginTransportError(
        PluginProblem(503, "SERVICE_UNAVAILABLE", "Service Unavailable", detail)
    )


# ---------------------------------------------------------------------------
# Таблица маршрутов: (capability, точечная operation, класс Pydantic-тела,
# фабрика доменного Request). Единственное место, где HTTP-путь связывается с
# typed-контрактом; произвольного «имя -> action» dispatch здесь НЕТ.
# ---------------------------------------------------------------------------


def _envelope(claims: PluginClaims) -> Any:
    """Серверная identity из claims (НЕ из тела/заголовков)."""
    from prodavan.application.plugins.capabilities.contracts import CapabilityEnvelope

    return CapabilityEnvelope(
        request_id=str(uuid4()),
        company_id=claims.company_id,
        cabinet_id=claims.cabinet_id,
        project_id=claims.project_id,
        subject_id=claims.sub,
        plugin_instance_id=claims.plugin_instance_id,
        artifact_digest=claims.artifact_digest,
        grant_id=claims.grant_id,
    )


def _contracts() -> Any:
    """Ленивый импорт доменных контрактов (чужой каталог задачи 03)."""
    from prodavan.application.plugins.capabilities import contracts

    return contracts


def _request_dataclass(name: str, body: Any, claims: PluginClaims) -> Any:
    """Собрать один Request-датакласс из Pydantic-тела + серверного envelope."""
    c = _contracts()
    cls = getattr(c, name)
    data = body.model_dump(exclude_none=True)
    return cls(**data, envelope=_envelope(claims))


def _cache_get(body: Any, claims: PluginClaims) -> Any:
    return _request_dataclass("CacheGetRequest", body, claims)


def _cache_set(body: Any, claims: PluginClaims) -> Any:
    return _request_dataclass("CacheSetRequest", body, claims)


def _cache_delete(body: Any, claims: PluginClaims) -> Any:
    return _request_dataclass("CacheDeleteRequest", body, claims)


def _cache_incr(body: Any, claims: PluginClaims) -> Any:
    return _request_dataclass("CacheIncrRequest", body, claims)


def _cache_publish(body: Any, claims: PluginClaims) -> Any:
    return _request_dataclass("CachePublishRequest", body, claims)


def _objects_credentials(body: Any, claims: PluginClaims) -> Any:
    c = _contracts()
    data = body.model_dump(exclude_none=True)
    return c.ObjectsScopedCredentialsRequest(
        bucket=data["bucket"],
        key_prefix=data["key_prefix"],
        permissions=frozenset(c.ObjectPermission(item) for item in data["permissions"]),
        ttl_seconds=data["ttl_seconds"],
        envelope=_envelope(claims),
    )


def _objects_list(body: Any, claims: PluginClaims) -> Any:
    return _request_dataclass("ObjectsListRequest", body, claims)


def _objects_delete(key: str, body: Any, claims: PluginClaims) -> Any:
    c = _contracts()
    # ``key`` из path — единственный источник; поле ``key`` в теле (если есть)
    # должно совпадать, иначе отказ (защита от расхождения path/body).
    declared = body.key
    if declared is not None and declared != key:
        raise ValueError("object key in body does not match path")
    return c.ObjectsDeleteRequest(
        bucket=body.bucket,
        object_key=key,
        envelope=_envelope(claims),
    )


def _docs_query(body: Any, claims: PluginClaims) -> Any:
    c = _contracts()
    data = body.model_dump(exclude_none=True)
    predicates = tuple(
        c.DocsPredicate(
            field=item["field"],
            operator=c.DocsQueryOperator(item["operator"]),
            values=tuple(item["values"]),
        )
        for item in data["predicates"]
    )
    return c.DocsQueryRequest(
        namespace=data["namespace"],
        collection=data["collection"],
        predicates=predicates,
        limit=data["limit"],
        projection=tuple(data.get("projection") or ()),
        sort_field=data.get("sort_field"),
        sort_order=c.DocsSortOrder(data.get("sort_order") or "asc"),
        envelope=_envelope(claims),
    )


def _events_publish(body: Any, claims: PluginClaims) -> Any:
    return _request_dataclass("EventsPublishRequest", body, claims)


def _jobs_submit(body: Any, claims: PluginClaims) -> Any:
    return _request_dataclass("JobsSubmitRequest", body, claims)


def _jobs_get(job_id: str, body: Any, claims: PluginClaims) -> Any:
    c = _contracts()
    return c.JobsGetRequest(job_id=job_id, run_id=body.run_id, envelope=_envelope(claims))


def _jobs_cancel(job_id: str, body: Any, claims: PluginClaims) -> Any:
    c = _contracts()
    return c.JobsCancelRequest(
        job_id=job_id,
        run_id=body.run_id,
        lease_id=body.lease_id,
        envelope=_envelope(claims),
    )


def _pods_invoke(body: Any, claims: PluginClaims) -> Any:
    c = _contracts()
    data = body.model_dump(exclude_none=True)
    return c.PodsInvokeRequest(
        pod_id=data["pod_id"],
        action=c.PodActionName(data["action"]),
        target_ref=data["target_ref"],
        arguments_digest=data.get("arguments_digest") or c.empty_digest(),
        envelope=_envelope(claims),
    )


def _mcp_register(body: Any, claims: PluginClaims) -> Any:
    c = _contracts()
    data = body.model_dump(exclude_none=True)
    tools = tuple(
        c.McpToolDefinition(
            tool_name=item["tool_name"],
            tool_version=item["tool_version"],
            capability=c.CapabilityName(item["capability"]),
            operation=item["operation"],
            input_schema_digest=item["input_schema_digest"],
        )
        for item in data["tools"]
    )
    return c.McpRegisterRequest(
        server_id=data["server_id"],
        tools=tools,
        envelope=_envelope(claims),
    )


def _secrets_read(body: Any, claims: PluginClaims) -> Any:
    return _request_dataclass("SecretsReadRequest", body, claims)


def _network_request(body: Any, claims: PluginClaims) -> Any:
    c = _contracts()
    data = body.model_dump(exclude_none=True)
    return c.NetworkEgressRequest(
        host=data["host"],
        port=data["port"],
        path=data["path"],
        method=c.NetworkHttpMethod(data["method"]),
        body_digest=data.get("body_digest") or c.empty_digest(),
        envelope=_envelope(claims),
    )


# Кэш-операция из path -> доменный Request. Закрытый перечень (§4.3):
# get/set/delete/incr/publish; неизвестная операция -> 404 через роутер.
CACHE_OPERATIONS: frozenset[str] = frozenset({"get", "set", "delete", "incr", "publish"})


def build_cache_request(op: str, body: Any, claims: PluginClaims) -> Any:
    """Собрать CacheXxxRequest по операции из path."""
    builders = {
        "get": _cache_get,
        "set": _cache_set,
        "delete": _cache_delete,
        "incr": _cache_incr,
        "publish": _cache_publish,
    }
    if op not in builders:
        raise ValueError(f"unknown cache operation: {op!r}")
    return builders[op](body, claims)


# ---------------------------------------------------------------------------
# Вызов сервиса и проекция результата
# ---------------------------------------------------------------------------


def _scalar(value: Any) -> Any:
    """Безопасная JSON-проекция: Enum -> value, dataclass -> dict, tuple -> list."""
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: _scalar(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_scalar(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _scalar(item) for key, item in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # Произвольный объект в ответ НЕ публикуем (fail-closed: только типы выше).
    return str(value)


def _decision_data(result: Any) -> dict[str, Any]:
    """Извлечь ``data`` из результата сервиса (TypedResponse / dict / None)."""
    data = getattr(result, "data", None)
    if data is None and isinstance(result, dict):
        data = result.get("data")
    if data is None:
        return {}
    if isinstance(data, dict):
        return _scalar(data)  # type: ignore[return-value]
    if dataclasses.is_dataclass(data) and not isinstance(data, type):
        return _scalar(data)  # type: ignore[return-value]
    # Нетипизированный результат не публикуем как успех.
    return {}


def _decision_code(result: Any) -> Any:
    return getattr(result, "code", None) if not isinstance(result, dict) else result.get("code")


def _decision_value(result: Any) -> Any:
    return getattr(result, "decision", None) if not isinstance(result, dict) else result.get("decision")


def invoke_capability(
    *,
    request: Request,
    claims: PluginClaims,
    service: CapabilityServiceLike,
    capability: str,
    operation: str,
    domain_request: Any,
) -> dict[str, Any]:
    """Вызвать сервис и вернуть ``{decision, data}`` либо поднять типизированную ошибку.

    Штатные deny НЕ исключения: ``result.code`` (QUOTA_EXCEEDED, GRANT_REVOKED,
    ADAPTER_MISSING, ...) маппится в 429/403/503/422/401. Исключения — только
    fallback для невалидного входа/непредвиденного сбоя; fail-closed.
    """
    try:
        result = service.invoke(
            claims=claims,
            capability=capability,
            operation=operation,
            request=domain_request,
        )
    except PluginTransportError:
        raise
    except Exception as exc:
        problem = problem_from_exception(exc)
        if problem is not None:
            raise PluginTransportError(problem) from exc
        raise PluginTransportError(
            PluginProblem(503, "ADAPTER_FAILED", "Adapter Failed", "capability invocation failed")
        ) from exc

    code = _decision_code(result)
    decision = _decision_value(result)
    detail = str(getattr(result, "reason", None) or "capability request denied")
    problem = problem_from_decision(decision, code, detail=detail)
    if problem is not None:
        raise PluginTransportError(problem)
    return {"decision": "allow", "data": _decision_data(result)}


def problem_response(problem: PluginProblem, request: Request) -> Any:
    """Тонкая обёртка для единообразия импортов в роутере."""
    return plugin_problem_response(problem, request)


CapabilityServiceDep = Depends(get_capability_service)

__all__ = [
    "CACHE_OPERATIONS",
    "CapabilityServiceDep",
    "CapabilityServiceLike",
    "build_cache_request",
    "get_capability_service",
    "invoke_capability",
    "problem_response",
]
