"""Порты capability-адаптеров плагинов (F2).

* Для КАЖДОГО адаптера — отдельный ``Protocol`` с типизированными методами.
  Ни один метод не принимает ``Mapping[str, Any]``, ``sql``, ``command``,
  ``args`` или произвольный ``payload`` (ADR-002, §4.3).
* ``AdapterRegistry`` — единственная точка разрешения адаптера. Неизвестное
  имя → ``AdapterMissingError``/``UnknownCapabilityError`` (fail-closed, §0.5 плана).
  ``db.direct`` в реестр НЕ регистрируется и resolve('db.direct') даёт отказ.
* ``PluginClaims`` / ``GrantRevocationPort`` описаны СТРУКТУРНО: конкретные
  реализации живут в задаче 02 (`application/plugins/identity/**`). Dataclass
  задачи 02 автоматически удовлетворит протоколу; правки чужого каталога
  из F2 не требуются. Отсутствие revocation-порта = отказ (fail-closed).
* Публичный формат операции — ТОЧЕЧНЫЙ (``cache.get``, реестр §4.4);
  внутри адаптера метод называется голой частью (``get``). Нормализация —
  единая точка в ``service._prepare`` и ``dispatch``; см. комментарии ниже.
"""

from __future__ import annotations

import inspect
from datetime import datetime
from typing import Awaitable, Final, Protocol, runtime_checkable

from prodavan.application.plugins.capabilities.contracts import (
    CacheDeleteRequest,
    CacheDeleteResponse,
    CacheGetRequest,
    CacheGetResponse,
    CacheIncrRequest,
    CacheIncrResponse,
    CachePublishRequest,
    CachePublishResponse,
    CacheSetRequest,
    CacheSetResponse,
    CapabilityName,
    DB_DIRECT,
    DocsQueryRequest,
    DocsQueryResponse,
    EventsPublishRequest,
    EventsPublishResponse,
    EventsSubscribeRequest,
    EventsSubscribeResponse,
    JobsCancelRequest,
    JobsCancelResponse,
    JobsGetRequest,
    JobsGetResponse,
    JobsSubmitRequest,
    JobsSubmitResponse,
    McpInvokeRequest,
    McpInvokeResponse,
    McpRegisterRequest,
    McpRegisterResponse,
    NetworkEgressRequest,
    NetworkEgressResponse,
    ObjectsDeleteRequest,
    ObjectsDeleteResponse,
    ObjectsListRequest,
    ObjectsListResponse,
    ObjectsPresignRequest,
    ObjectsPresignResponse,
    ObjectsScopedCredentialsRequest,
    ObjectsScopedCredentialsResponse,
    OPERATION_TO_CAPABILITY,
    PodsInvokeRequest,
    PodsInvokeResponse,
    PluginCapabilityRequest,
    SecretsReadRequest,
    SecretsReadResponse,
    TypedContractError,
    TypedResponse,
)
from prodavan.application.plugins.capabilities.quotas import QuotaUsage


class AdapterMissingError(LookupError):
    """Адаптер не зарегистрирован; запрос отклоняется, а не «тихо пропускается».

    Fail-closed: отсутствие интеграции — явная ошибка. Код решения ядра —
    ``DecisionCode.ADAPTER_MISSING``; HTTP-проекция — 503/403, НИКОГДА allow.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"capability adapter is not registered: {name!r}")


class UnknownCapabilityError(ValueError):
    """Имя capability не входит в единый реестр §4.4 (включая ``db.direct``)."""

    def __init__(self, name: str) -> None:
        self.name = name
        if name == DB_DIRECT:
            super().__init__(
                "db.direct is not a gateway capability: plugin database access is a "
                "direct DSN bounded by PostgreSQL privileges, not an adapter call"
            )
        else:
            super().__init__(f"unknown plugin capability: {name!r}")


# ---------------------------------------------------------------------------
# Identity / revocation (задача 02). Структурные протоколы, без импорта чужого
# каталога: F2 не зависит от порядка создания файлов в параллельной задаче.
# ---------------------------------------------------------------------------


@runtime_checkable
class PluginClaims(Protocol):
    """СТРУКТУРНЫЙ минимум проверенного identity плагина (§4.2).

    Намеренно описан только необходимыми F2 полями. Реальный ``PluginClaims``
    из задачи 02 НЕ имеет ``subject_id`` (там ``sub``), поэтому требовать его
    здесь было бы ошибкой стыка: протокол требует ровно ``sub``, ``company_id``,
    ``grant_id``, ``scopes``, ``artifact_digest``, ``plugin_instance_id``.
    Полный JSON-контракт (iss/aud/typ/jti/exp/cabinet/project/manifest_version)
    остаётся в задаче 02; F2 читает его опционально через getattr.

    Токен несёт ТОЛЬКО ``grant_id`` и короткий TTL: egress-политика и квоты
    хранятся в ``CapabilityGrant`` в БД, а не в JWT (§13.3, acceptance №22).
    """

    @property
    def sub(self) -> str: ...
    @property
    def company_id(self) -> str: ...
    @property
    def plugin_instance_id(self) -> str: ...
    @property
    def artifact_digest(self) -> str: ...
    @property
    def grant_id(self) -> str: ...
    @property
    def scopes(self) -> frozenset[str] | tuple[str, ...]: ...


class GrantRevocationPort(Protocol):
    """Проверка активности grant'а по ``grant_id`` на КАЖДОМ запросе (кр. 12).

    Реализация задачи 02 (или тонкий адаптер при F11-интеграции). Любая ошибка
    порта трактуется сервисом как «не активен» → отказ, а не allow.
    """

    async def is_active(self, grant_id: str, *, now: datetime) -> bool: ...


# ---------------------------------------------------------------------------
# Протоколы адаптеров. Методы возвращают ТОЛЬКО типизированные Response.
# ---------------------------------------------------------------------------


class QuotaMeterPort(Protocol):
    """Read-only pre-invoke hook реального адаптера или его decorator'а.

    Вызывается один раз до reserve и до side effect. Реализация обязана вернуть
    все шесть явно измеренных единиц; ``ops`` всегда равен 1. Единицы:
    ``ROWS`` — строки результата (или bound из typed limit), ``BYTES`` — октеты
    typed payload, ``KEYS`` — distinct touched keys, ``OBJECT_BYTES`` — октеты
    объектного содержимого, ``EGRESS_BYTES`` — request + response octets.
    Для заранее неизвестных ROWS/OBJECT_BYTES/EGRESS_BYTES возвращается точный
    или консервативный server-side reservation bound из metadata/typed limit,
    а не fabricated zero. Ноль разрешён только для действительно неприменимой
    единицы. Отсутствие hook, исключение или неверный тип = deny до reserve.
    """

    def quota_usage(
        self, request: PluginCapabilityRequest
    ) -> QuotaUsage | Awaitable[QuotaUsage]: ...


class CachePort(Protocol):
    """get/set/delete/incr/publish с key-rewrite (§4.3)."""

    async def get(self, request: CacheGetRequest) -> CacheGetResponse: ...
    async def set(self, request: CacheSetRequest) -> CacheSetResponse: ...
    async def delete(self, request: CacheDeleteRequest) -> CacheDeleteResponse: ...
    async def incr(self, request: CacheIncrRequest) -> CacheIncrResponse: ...
    async def publish(self, request: CachePublishRequest) -> CachePublishResponse: ...


class ObjectsPort(Protocol):
    """scoped credentials / exact-key presign / list / delete.

    Содержимое объекта (base64-байты) через API НЕ передаётся: только
    short-lived scoped credentials, presigned-ссылки и метаданные (§4.3, §5.4).
    """

    async def scoped_credentials(
        self, request: ObjectsScopedCredentialsRequest
    ) -> ObjectsScopedCredentialsResponse: ...
    async def presign(self, request: ObjectsPresignRequest) -> ObjectsPresignResponse: ...
    async def list(self, request: ObjectsListRequest) -> ObjectsListResponse: ...
    async def delete(self, request: ObjectsDeleteRequest) -> ObjectsDeleteResponse: ...


class DocsPort(Protocol):
    """Mongo namespace per tenant, строго типизированный запрос (§4.3)."""

    async def query(self, request: DocsQueryRequest) -> DocsQueryResponse: ...


class EventsPort(Protocol):
    """publish/subscribe через Redpanda consumer groups (§4.3, §7.3)."""

    async def publish(self, request: EventsPublishRequest) -> EventsPublishResponse: ...
    async def subscribe(self, request: EventsSubscribeRequest) -> EventsSubscribeResponse: ...


class JobsPort(Protocol):
    """submit/get/cancel с ``job_id``, ``run_id`` и lease (§4.3, §7.3)."""

    async def submit(self, request: JobsSubmitRequest) -> JobsSubmitResponse: ...
    async def get(self, request: JobsGetRequest) -> JobsGetResponse: ...
    async def cancel(self, request: JobsCancelRequest) -> JobsCancelResponse: ...


class PodsPort(Protocol):
    """invoke typed action; плагин НЕ имеет k8s API (§4.3)."""

    async def invoke(self, request: PodsInvokeRequest) -> PodsInvokeResponse: ...


class McpPort(Protocol):
    """Register and invoke through the typed backend broker, never generic infra dispatch."""

    async def register(self, request: McpRegisterRequest) -> McpRegisterResponse: ...
    async def invoke(self, request: McpInvokeRequest) -> McpInvokeResponse: ...


class SecretsPort(Protocol):
    """read по ``secret_ref``; plaintext не покидает защищённый контур (§4.3)."""

    async def read(self, request: SecretsReadRequest) -> SecretsReadResponse: ...


class NetworkPort(Protocol):
    """egress-прокси; allowed_hosts разрешаются из grant'а в БД (§6, §13.3)."""

    async def egress(self, request: NetworkEgressRequest) -> NetworkEgressResponse: ...


#: Имя capability -> протокол адаптера. Используется для проверки register().
CAPABILITY_PORT: Final[dict[str, type]] = {
    CapabilityName.CACHE.value: CachePort,
    CapabilityName.OBJECTS.value: ObjectsPort,
    CapabilityName.DOCS.value: DocsPort,
    CapabilityName.EVENTS.value: EventsPort,
    CapabilityName.JOBS.value: JobsPort,
    CapabilityName.PODS.value: PodsPort,
    CapabilityName.MCP.value: McpPort,
    CapabilityName.SECRETS.value: SecretsPort,
    CapabilityName.NETWORK.value: NetworkPort,
}

#: Имя capability -> ГОЛЫЕ имена методов адаптера (внутренний контракт).
#: Публичный реестр операций — ТОЧЕЧНЫЙ ``OPERATION_TO_CAPABILITY`` (§4.4):
#: ``cache.get``, ``network.egress`` и т.д. Задача 04 шлёт точечную форму,
#: ``PluginCapabilityService._prepare`` нормализует её в голую часть перед
#: передачей сюда. Оба формата допустимы в ``dispatch`` ради совместимости.
CAPABILITY_OPERATIONS: Final[dict[str, frozenset[str]]] = {
    CapabilityName.CACHE.value: frozenset({"get", "set", "delete", "incr", "publish"}),
    CapabilityName.OBJECTS.value: frozenset({"scoped_credentials", "presign", "list", "delete"}),
    CapabilityName.DOCS.value: frozenset({"query"}),
    CapabilityName.EVENTS.value: frozenset({"publish", "subscribe"}),
    CapabilityName.JOBS.value: frozenset({"submit", "get", "cancel"}),
    CapabilityName.PODS.value: frozenset({"invoke"}),
    CapabilityName.MCP.value: frozenset({"register", "invoke"}),
    CapabilityName.SECRETS.value: frozenset({"read"}),
    CapabilityName.NETWORK.value: frozenset({"egress"}),
}

#: Точечная форма для проверок (строится из публичного реестра §4.4).
CAPABILITY_DOTTED_OPERATIONS: Final[dict[str, frozenset[str]]] = {
    name: frozenset(f"{name}.{op}" for op in ops)
    for name, ops in CAPABILITY_OPERATIONS.items()
}


OPERATION_REQUEST_TYPES: Final[dict[str, type]] = {
    "cache.get": CacheGetRequest,
    "cache.set": CacheSetRequest,
    "cache.delete": CacheDeleteRequest,
    "cache.incr": CacheIncrRequest,
    "cache.publish": CachePublishRequest,
    "objects.scoped_credentials": ObjectsScopedCredentialsRequest,
    "objects.presign": ObjectsPresignRequest,
    "objects.list": ObjectsListRequest,
    "objects.delete": ObjectsDeleteRequest,
    "docs.query": DocsQueryRequest,
    "events.publish": EventsPublishRequest,
    "events.subscribe": EventsSubscribeRequest,
    "jobs.submit": JobsSubmitRequest,
    "jobs.get": JobsGetRequest,
    "jobs.cancel": JobsCancelRequest,
    "pods.invoke": PodsInvokeRequest,
    "mcp.register": McpRegisterRequest,
    "mcp.invoke": McpInvokeRequest,
    "secrets.read": SecretsReadRequest,
    "network.egress": NetworkEgressRequest,
}



class AdapterRegistry:
    """Fail-closed реестр адаптеров.

    ``register`` принимает только известное имя из §4.4 и реализацию, у которой
    есть все методы, объявленные протоколом. ``resolve`` неизвестного имени
    (включая ``db.direct``) всегда бросает ``AdapterMissingError``/``UnknownCapabilityError``.
    Повторная регистрация того же имени запрещена: конфигурационная ошибка
    должна быть заметна, а не «последний победил».
    """

    def __init__(self) -> None:
        self._adapters: dict[str, object] = {}

    def register(self, name: str, impl: object) -> None:
        # db.direct в gateway принципиально отсутствует (§4.3, ADR-007).
        if name == DB_DIRECT or name not in CAPABILITY_PORT:
            raise UnknownCapabilityError(name)
        if name in self._adapters:
            raise ValueError(f"capability adapter already registered: {name!r}")
        protocol = CAPABILITY_PORT[name]
        missing = [
            method for method in CAPABILITY_OPERATIONS[name]
            if not callable(getattr(impl, method, None))
        ]
        if missing:
            raise TypeError(
                f"adapter {name!r} does not implement {protocol.__name__}: "
                f"missing methods {sorted(missing)}"
            )
        self._adapters[name] = impl

    def resolve(self, name: str) -> object:
        """Вернуть адаптер или отказать. Молчаливого ``None`` здесь нет."""
        if name == DB_DIRECT or name not in CAPABILITY_PORT:
            raise UnknownCapabilityError(name)
        try:
            return self._adapters[name]
        except KeyError as exc:
            raise AdapterMissingError(name) from exc

    @property
    def registered(self) -> frozenset[str]:
        """Только для диагностики; не используется как источник разрешения."""
        return frozenset(self._adapters)


async def dispatch(adapter: object, capability: str, operation: str, request: object) -> TypedResponse:
    """Единственная точка typed dispatch: capability + operation + Request.

    КАНОНИЧЕСКИЙ ФОРМАТ ОПЕРАЦИИ — ТОЧЕЧНЫЙ (``cache.get``, реестр
    ``contracts.OPERATION_TO_CAPABILITY`` из §4.4); задача 04 передаёт именно
    его. Внутри адаптера метод называется голой частью (``get``), поэтому
    ``dispatch`` отсекает префикс capability через ``split(".", 1)[-1]``.
    Голая форма (``get``) тоже принимается для обратной совместимости.

    Это НЕ ``{"command": ..., "args": ...}``: операция проверяется по
    закрытому множеству, а ``request`` — один из датаклассов contracts.py.
    """
    dotted = operation if "." in operation else f"{capability}.{operation}"
    allowed = CAPABILITY_OPERATIONS.get(capability, frozenset()) | CAPABILITY_DOTTED_OPERATIONS.get(capability, frozenset())
    if (
        dotted not in OPERATION_TO_CAPABILITY
        or OPERATION_TO_CAPABILITY[dotted] != capability
        or operation not in allowed
    ):
        raise UnknownCapabilityError(f"{capability}/{operation}")
    expected_request = OPERATION_REQUEST_TYPES[dotted]
    if not isinstance(request, expected_request):
        raise TypedContractError(
            f"{dotted} requires {expected_request.__name__}, got {type(request).__name__}"
        )
    method_name = dotted.split(".", 1)[-1]
    method = getattr(adapter, method_name, None)
    if not callable(method):
        raise AdapterMissingError(capability)
    response = method(request)
    if inspect.isawaitable(response):
        response = await response
    if not isinstance(response, TypedResponse):
        # Адаптер вернул «что угодно» — не публикуем как успех (fail-closed).
        raise TypeError(f"adapter {capability!r} returned a non-typed response")
    return response


__all__ = [
    "AdapterMissingError",
    "AdapterRegistry",
    "CAPABILITY_DOTTED_OPERATIONS",
    "CAPABILITY_OPERATIONS",
    "CAPABILITY_PORT",
    "OPERATION_REQUEST_TYPES",
    "CachePort",
    "DocsPort",
    "EventsPort",
    "GrantRevocationPort",
    "JobsPort",
    "McpPort",
    "NetworkPort",
    "ObjectsPort",
    "PodsPort",
    "PluginClaims",
    "QuotaMeterPort",
    "SecretsPort",
    "UnknownCapabilityError",
    "dispatch",
]
