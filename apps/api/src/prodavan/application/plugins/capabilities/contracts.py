"""Типизированные контракты capability-адаптеров плагинов (F2).

Правила слоя (источник: docs/ARCHITECTURE/plugin-architecture-complete.md v4
§4.3, §4.4; ADR-002-capability-gateway):

* Каждый адаптер описывается ЯВНЫМИ полями Request/Response. Никакого generic
  dispatch: здесь нет и не будет ``{"sql": ...}``, ``{"command": ..., "args": ...}``,
  ``params: dict[str, Any]``, ``payload: bytes`` или ``body``. ADR-002 запрещает
  «generic row/action dispatch как публичный контракт».
* ``db.direct`` НЕ представлен среди адаптеров: БД плагина — это прямой DSN к
  своей БД компании (граница = отдельная БД + роль PostgreSQL + NetworkPolicy),
  а не вызов gateway. См. §2, §4.3, ADR-007.
* Значения несут только идентификаторы/метаданные. Сырые байты объектов,
  plaintext секретов, токены и PII через контракты не передаются.
* Все контракты неизменяемые (frozen slots) и fail-closed: пустые/отрицательные
  значения отклоняются на границе, до попадания в ядро gateway.

Комментарии и причины отказа — на русском; идентификаторы — на английском.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final
from uuid import uuid4

# ---------------------------------------------------------------------------
# Единый реестр идентификаторов (§4.4). Имена capability фиксируются здесь.
# ---------------------------------------------------------------------------


class CapabilityName(StrEnum):
    """Разрешённые capability плагина, проходящие через gateway.

    ``DB_DIRECT`` намеренно ОТСУТСТВУЕТ: прямой DSN не является capability
    gateway и не может быть вызван через ``/capabilities/v1/*``.
    """

    CACHE = "cache"
    OBJECTS = "objects"
    DOCS = "docs"
    EVENTS = "events"
    JOBS = "jobs"
    PODS = "pods"
    MCP = "mcp"
    SECRETS = "secrets"
    NETWORK = "network"


#: Имя capability, которое принципиально не проходит через gateway (§4.3).
DB_DIRECT: Final[str] = "db.direct"

#: Полный набор capability, доступных плагину (для валидации scope/grant).
PLUGIN_CAPABILITIES: Final[frozenset[str]] = frozenset(item.value for item in CapabilityName)


class AdapterName(StrEnum):
    """Имена адаптеров в ``AdapterRegistry`` — совпадают с capability."""

    CACHE = "cache"
    OBJECTS = "objects"
    DOCS = "docs"
    EVENTS = "events"
    JOBS = "jobs"
    PODS = "pods"
    MCP = "mcp"
    SECRETS = "secrets"
    NETWORK = "network"


#: «Операция -> capability» для проверки, что typed контракт не подменили.
#: Используется сервисом и тестами; произвольная строка операцией не является.
OPERATION_TO_CAPABILITY: Final[dict[str, str]] = {
    "cache.get": "cache",
    "cache.set": "cache",
    "cache.delete": "cache",
    "cache.incr": "cache",
    "cache.publish": "cache",
    "objects.scoped_credentials": "objects",
    "objects.list": "objects",
    "objects.delete": "objects",
    "docs.query": "docs",
    "events.publish": "events",
    "events.subscribe": "events",
    "jobs.submit": "jobs",
    "jobs.get": "jobs",
    "jobs.cancel": "jobs",
    "pods.invoke": "pods",
    "mcp.register": "mcp",
    "secrets.read": "secrets",
    "network.egress": "network",
}


# ---------------------------------------------------------------------------
# Общие примитивы
# ---------------------------------------------------------------------------


class TypedContractError(ValueError):
    """Контракт не может быть безопасно представлен (fail-closed)."""


def _require_text(value: str, name: str) -> None:
    """Непустая строка без управляющих переносов (защита от инъекции в ключах)."""
    if not isinstance(value, str) or not value.strip():
        raise TypedContractError(f"{name} must be a non-empty string")
    if any(char in value for char in ("\x00", "\n", "\r")):
        raise TypedContractError(f"{name} must not contain control characters")


def _require_positive(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise TypedContractError(f"{name} must be a positive integer")


def _require_non_negative(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise TypedContractError(f"{name} must be a non-negative integer")


def _require_digest(value: str, name: str) -> None:
    """Канонический digest ``sha256:<64 hex>``; сами данные не передаём."""
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise TypedContractError(f"{name} must be a sha256 digest")
    if any(char not in "0123456789abcdef" for char in value[7:]):
        raise TypedContractError(f"{name} must contain lowercase hex only")


@dataclass(frozen=True, slots=True, kw_only=True)
class CapabilityEnvelope:
    """Серверная идентичность запроса; НЕ заполняется из тела плагина.

    Поля приходят из проверенного ``PluginClaims`` (задача 02). Плагин не может
    подставить ``company_id`` / ``artifact_digest`` / ``grant_id`` через контракт
    адаптера: они задаются сервисом на стороне платформы.
    """

    request_id: str = field(default_factory=lambda: str(uuid4()))
    correlation_id: str | None = None
    run_id: str | None = None
    company_id: str = ""
    cabinet_id: str | None = None
    project_id: str | None = None
    subject_id: str = ""
    plugin_instance_id: str = ""
    artifact_digest: str = ""
    grant_id: str = ""

    def __post_init__(self) -> None:
        for name in ("request_id", "company_id", "subject_id", "plugin_instance_id", "grant_id"):
            _require_text(getattr(self, name), name)
        _require_digest(self.artifact_digest, "artifact_digest")
        for name in ("correlation_id", "run_id", "cabinet_id", "project_id"):
            value = getattr(self, name)
            if value is not None:
                _require_text(value, name)


@dataclass(frozen=True, slots=True, kw_only=True)
class TypedResponse:
    """Базовый тип ответа адаптера: только статус и канонический digest.

    Ни одного поля с сырыми данными: ``ok``/``found``/``code`` и ``digest``
    (канонический sha256 от содержимого, вычисленный адаптером) — безопасная
    проекция для аудита. Плагин получает данные от адаптера вне аудита, но
    контракт аудита их никогда не содержит.
    """

    request_id: str
    ok: bool
    digest: str

    def __post_init__(self) -> None:
        _require_text(self.request_id, "request_id")
        _require_digest(self.digest, "digest")


def empty_digest() -> str:
    """Канонический digest пустого результата (для ``not found``/``no body``)."""
    return "sha256:" + "0" * 64


# ---------------------------------------------------------------------------
# cache: get / set / delete / incr / publish c key-rewrite (§4.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheGetRequest:
    key: str
    envelope: CapabilityEnvelope

    def __post_init__(self) -> None:
        _require_text(self.key, "key")


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheGetResponse(TypedResponse):
    found: bool = False
    value_digest: str = field(default_factory=empty_digest)

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_digest(self.value_digest, "value_digest")


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheSetRequest:
    """``value`` — строка, длина ограничена квотой ``max_bytes``/``max_keys``.

    НЕ ``dict`` и НЕ ``bytes``/base64: тип поля фиксирован, generic payload
    запрещён. Таймаут (TTL) задаётся сервером, а не полем из тела (§4.4).
    """

    key: str
    value: str
    envelope: CapabilityEnvelope

    def __post_init__(self) -> None:
        _require_text(self.key, "key")
        if not isinstance(self.value, str):
            raise TypedContractError("value must be a string")


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheSetResponse(TypedResponse):
    stored_bytes: int = 0

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_non_negative(self.stored_bytes, "stored_bytes")


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheDeleteRequest:
    key: str
    envelope: CapabilityEnvelope

    def __post_init__(self) -> None:
        _require_text(self.key, "key")


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheDeleteResponse(TypedResponse):
    deleted: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheIncrRequest:
    key: str
    amount: int = 1
    envelope: CapabilityEnvelope

    def __post_init__(self) -> None:
        _require_text(self.key, "key")
        if not isinstance(self.amount, int) or isinstance(self.amount, bool):
            raise TypedContractError("amount must be an integer")


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheIncrResponse(TypedResponse):
    value: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class CachePublishRequest:
    """Pub/Sub через cache: канал + канонический digest сообщения.

    Тело сообщения не входит в контракт gateway (и не попадает в аудит).
    """

    channel: str
    message_digest: str
    envelope: CapabilityEnvelope

    def __post_init__(self) -> None:
        _require_text(self.channel, "channel")
        _require_digest(self.message_digest, "message_digest")


@dataclass(frozen=True, slots=True, kw_only=True)
class CachePublishResponse(TypedResponse):
    subscribers: int = 0

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_non_negative(self.subscribers, "subscribers")


# ---------------------------------------------------------------------------
# objects: scoped_credentials / list / delete (§4.3, §5.4)
# НИКАКИХ base64-байтов через API: только short-lived credentials / presigned.
# ---------------------------------------------------------------------------


class ObjectPermission(StrEnum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"


@dataclass(frozen=True, slots=True, kw_only=True)
class ScopedCredentials:
    """Short-lived STS-подобные учётные данные с session policy.

    Поля — только opaque-ссылки и сроки. Сам ``secret`` здесь НЕ хранится:
    адаптер выдаёт его по внутреннему защищённому каналу, а в аудит уходит
    лишь ``credentials_ref`` + digest политики.
    """

    credentials_ref: str
    bucket: str
    key_prefix: str
    expires_at_epoch: int
    policy_digest: str

    def __post_init__(self) -> None:
        for name in ("credentials_ref", "bucket", "key_prefix"):
            _require_text(getattr(self, name), name)
        _require_positive(self.expires_at_epoch, "expires_at_epoch")
        _require_digest(self.policy_digest, "policy_digest")


@dataclass(frozen=True, slots=True, kw_only=True)
class PresignedObject:
    """Scoped presigned URL: объект идентифицируется по ключу и digest'у."""

    object_key: str
    url_ref: str
    expires_at_epoch: int
    content_digest: str

    def __post_init__(self) -> None:
        _require_text(self.object_key, "object_key")
        _require_text(self.url_ref, "url_ref")
        _require_positive(self.expires_at_epoch, "expires_at_epoch")
        _require_digest(self.content_digest, "content_digest")


@dataclass(frozen=True, slots=True, kw_only=True)
class ObjectsScopedCredentialsRequest:
    bucket: str
    key_prefix: str
    permissions: frozenset[ObjectPermission]
    ttl_seconds: int
    envelope: CapabilityEnvelope

    def __post_init__(self) -> None:
        _require_text(self.bucket, "bucket")
        _require_text(self.key_prefix, "key_prefix")
        if not isinstance(self.permissions, frozenset) or not self.permissions:
            raise TypedContractError("permissions must be a non-empty frozenset")
        if any(not isinstance(item, ObjectPermission) for item in self.permissions):
            raise TypedContractError("permissions must contain ObjectPermission values")
        _require_positive(self.ttl_seconds, "ttl_seconds")
        # TTL — серверное ограничение, но верхняя граница фиксирована контрактом,
        # чтобы плагин не запросил «вечные» credentials.
        if self.ttl_seconds > 3600:
            raise TypedContractError("ttl_seconds must not exceed 3600")


@dataclass(frozen=True, slots=True, kw_only=True)
class ObjectsScopedCredentialsResponse(TypedResponse):
    credentials: ScopedCredentials | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ObjectsListRequest:
    bucket: str
    key_prefix: str
    limit: int
    cursor: str | None = None
    envelope: CapabilityEnvelope

    def __post_init__(self) -> None:
        _require_text(self.bucket, "bucket")
        _require_text(self.key_prefix, "key_prefix")
        _require_positive(self.limit, "limit")
        if self.limit > 1000:
            raise TypedContractError("limit must not exceed 1000")
        if self.cursor is not None:
            _require_text(self.cursor, "cursor")


@dataclass(frozen=True, slots=True, kw_only=True)
class ObjectRef:
    """Метаданные объекта без содержимого (§4.3: не base64-байты через API)."""

    object_key: str
    size_bytes: int
    content_digest: str

    def __post_init__(self) -> None:
        _require_text(self.object_key, "object_key")
        _require_non_negative(self.size_bytes, "size_bytes")
        _require_digest(self.content_digest, "content_digest")


@dataclass(frozen=True, slots=True, kw_only=True)
class ObjectsListResponse(TypedResponse):
    objects: tuple[ObjectRef, ...] = ()
    next_cursor: str | None = None
    total_bytes: int = 0

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.objects, tuple) or any(not isinstance(item, ObjectRef) for item in self.objects):
            raise TypedContractError("objects must be a tuple of ObjectRef")
        _require_non_negative(self.total_bytes, "total_bytes")
        if self.next_cursor is not None:
            _require_text(self.next_cursor, "next_cursor")


@dataclass(frozen=True, slots=True, kw_only=True)
class ObjectsDeleteRequest:
    bucket: str
    object_key: str
    envelope: CapabilityEnvelope

    def __post_init__(self) -> None:
        _require_text(self.bucket, "bucket")
        _require_text(self.object_key, "object_key")


@dataclass(frozen=True, slots=True, kw_only=True)
class ObjectsDeleteResponse(TypedResponse):
    deleted: bool = False


# ---------------------------------------------------------------------------
# docs: typed query (§4.3). Никакого произвольного фильтра/Mongo-JSON.
# ---------------------------------------------------------------------------


class DocsQueryOperator(StrEnum):
    EQ = "eq"
    IN = "in"
    RANGE = "range"
    EXISTS = "exists"


@dataclass(frozen=True, slots=True, kw_only=True)
class DocsPredicate:
    """Одно типизированное условие: поле, оператор, список скалярных значений."""

    field: str
    operator: DocsQueryOperator
    values: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.field, "field")
        if not isinstance(self.operator, DocsQueryOperator):
            raise TypedContractError("operator must be a DocsQueryOperator")
        if not isinstance(self.values, tuple) or not self.values:
            raise TypedContractError("values must be a non-empty tuple of strings")
        if any(not isinstance(item, str) for item in self.values):
            raise TypedContractError("values must contain strings only")
        if self.operator is DocsQueryOperator.RANGE and len(self.values) != 2:
            raise TypedContractError("range requires exactly two bounds")


class DocsSortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


@dataclass(frozen=True, slots=True, kw_only=True)
class DocsQueryRequest:
    namespace: str
    collection: str
    predicates: tuple[DocsPredicate, ...]
    limit: int
    projection: tuple[str, ...] = ()
    sort_field: str | None = None
    sort_order: DocsSortOrder = DocsSortOrder.ASC
    envelope: CapabilityEnvelope

    def __post_init__(self) -> None:
        _require_text(self.namespace, "namespace")
        _require_text(self.collection, "collection")
        if not isinstance(self.predicates, tuple) or any(not isinstance(item, DocsPredicate) for item in self.predicates):
            raise TypedContractError("predicates must be a tuple of DocsPredicate")
        _require_positive(self.limit, "limit")
        if self.limit > 1000:
            raise TypedContractError("limit must not exceed 1000")
        if not isinstance(self.projection, tuple) or any(not isinstance(item, str) for item in self.projection):
            raise TypedContractError("projection must be a tuple of field names")
        if self.sort_field is not None:
            _require_text(self.sort_field, "sort_field")
        if not isinstance(self.sort_order, DocsSortOrder):
            raise TypedContractError("sort_order must be a DocsSortOrder")


@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentRef:
    """Ссылка на документ: идентификатор + digest содержимого, без тела."""

    document_id: str
    content_digest: str

    def __post_init__(self) -> None:
        _require_text(self.document_id, "document_id")
        _require_digest(self.content_digest, "content_digest")


@dataclass(frozen=True, slots=True, kw_only=True)
class DocsQueryResponse(TypedResponse):
    documents: tuple[DocumentRef, ...] = ()
    matched: int = 0

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.documents, tuple) or any(not isinstance(item, DocumentRef) for item in self.documents):
            raise TypedContractError("documents must be a tuple of DocumentRef")
        _require_non_negative(self.matched, "matched")


# ---------------------------------------------------------------------------
# events: publish / subscribe (§4.3, §7.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class EventsPublishRequest:
    topic: str
    event_type: str
    partition_key: str
    payload_digest: str
    envelope: CapabilityEnvelope

    def __post_init__(self) -> None:
        for name in ("topic", "event_type", "partition_key"):
            _require_text(getattr(self, name), name)
        _require_digest(self.payload_digest, "payload_digest")


@dataclass(frozen=True, slots=True, kw_only=True)
class EventsPublishResponse(TypedResponse):
    event_id: str = ""
    partition: int = 0

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_text(self.event_id, "event_id")
        _require_non_negative(self.partition, "partition")


class EventsStartOffset(StrEnum):
    EARLIEST = "earliest"
    LATEST = "latest"


@dataclass(frozen=True, slots=True, kw_only=True)
class EventsSubscribeRequest:
    topic: str
    consumer_group: str
    subscription_id: str
    event_types: tuple[str, ...]
    start_offset: EventsStartOffset = EventsStartOffset.EARLIEST
    max_retries: int = 3
    envelope: CapabilityEnvelope

    def __post_init__(self) -> None:
        _require_text(self.topic, "topic")
        _require_text(self.consumer_group, "consumer_group")
        _require_text(self.subscription_id, "subscription_id")
        if not isinstance(self.event_types, tuple) or not self.event_types:
            raise TypedContractError("event_types must be a non-empty tuple")
        if any(not isinstance(item, str) or not item.strip() for item in self.event_types):
            raise TypedContractError("event_types must contain non-empty strings")
        if not isinstance(self.start_offset, EventsStartOffset):
            raise TypedContractError("start_offset must be an EventsStartOffset")
        if self.max_retries not in (1, 2, 3):
            raise TypedContractError("max_retries must be 1, 2 or 3")


@dataclass(frozen=True, slots=True, kw_only=True)
class EventsSubscribeResponse(TypedResponse):
    subscription_id: str = ""
    manual_commit: bool = True

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_text(self.subscription_id, "subscription_id")


# ---------------------------------------------------------------------------
# jobs: submit / get / cancel с job_id, run_id, lease (§4.3, §7.3)
# ---------------------------------------------------------------------------


class JobState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True, kw_only=True)
class JobLease:
    """Lease активного job'а: не даёт backend уснуть посреди работы (кр. 14)."""

    lease_id: str
    lease_owner: str
    expires_at_epoch: int

    def __post_init__(self) -> None:
        _require_text(self.lease_id, "lease_id")
        _require_text(self.lease_owner, "lease_owner")
        _require_positive(self.expires_at_epoch, "expires_at_epoch")


@dataclass(frozen=True, slots=True, kw_only=True)
class JobsSubmitRequest:
    task_name: str
    run_id: str
    arguments_digest: str
    envelope: CapabilityEnvelope

    def __post_init__(self) -> None:
        _require_text(self.task_name, "task_name")
        _require_text(self.run_id, "run_id")
        _require_digest(self.arguments_digest, "arguments_digest")


@dataclass(frozen=True, slots=True, kw_only=True)
class JobsSubmitResponse(TypedResponse):
    job_id: str = ""
    run_id: str = ""
    state: JobState = JobState.PENDING
    lease: JobLease | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_text(self.job_id, "job_id")
        _require_text(self.run_id, "run_id")
        if not isinstance(self.state, JobState):
            raise TypedContractError("state must be a JobState")


@dataclass(frozen=True, slots=True, kw_only=True)
class JobsGetRequest:
    job_id: str
    run_id: str
    envelope: CapabilityEnvelope

    def __post_init__(self) -> None:
        _require_text(self.job_id, "job_id")
        _require_text(self.run_id, "run_id")


@dataclass(frozen=True, slots=True, kw_only=True)
class JobsGetResponse(TypedResponse):
    job_id: str = ""
    run_id: str = ""
    state: JobState = JobState.PENDING
    result_digest: str = field(default_factory=empty_digest)
    lease: JobLease | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_text(self.job_id, "job_id")
        _require_text(self.run_id, "run_id")
        if not isinstance(self.state, JobState):
            raise TypedContractError("state must be a JobState")
        _require_digest(self.result_digest, "result_digest")


@dataclass(frozen=True, slots=True, kw_only=True)
class JobsCancelRequest:
    job_id: str
    run_id: str
    lease_id: str
    envelope: CapabilityEnvelope

    def __post_init__(self) -> None:
        _require_text(self.job_id, "job_id")
        _require_text(self.run_id, "run_id")
        _require_text(self.lease_id, "lease_id")


@dataclass(frozen=True, slots=True, kw_only=True)
class JobsCancelResponse(TypedResponse):
    job_id: str = ""
    state: JobState = JobState.CANCELLED

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_text(self.job_id, "job_id")
        if not isinstance(self.state, JobState):
            raise TypedContractError("state must be a JobState")


# ---------------------------------------------------------------------------
# pods: invoke typed action (§4.3). Плагин НЕ имеет k8s API и НЕ шлёт команды.
# ---------------------------------------------------------------------------


class PodActionName(StrEnum):
    """Закрытый перечень действий; generic ``command``/``args`` запрещён."""

    READ_STATUS = "read_status"
    READ_LOGS = "read_logs"
    SEND_AGENT_MESSAGE = "send_agent_message"
    READ_ARTIFACT = "read_artifact"


@dataclass(frozen=True, slots=True, kw_only=True)
class PodsInvokeRequest:
    pod_id: str
    action: PodActionName
    target_ref: str
    arguments_digest: str = field(default_factory=empty_digest)
    envelope: CapabilityEnvelope

    def __post_init__(self) -> None:
        _require_text(self.pod_id, "pod_id")
        if not isinstance(self.action, PodActionName):
            raise TypedContractError("action must be a PodActionName")
        _require_text(self.target_ref, "target_ref")
        _require_digest(self.arguments_digest, "arguments_digest")


@dataclass(frozen=True, slots=True, kw_only=True)
class PodsInvokeResponse(TypedResponse):
    pod_id: str = ""
    action: PodActionName = PodActionName.READ_STATUS
    result_digest: str = field(default_factory=empty_digest)

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_text(self.pod_id, "pod_id")
        if not isinstance(self.action, PodActionName):
            raise TypedContractError("action must be a PodActionName")
        _require_digest(self.result_digest, "result_digest")


# ---------------------------------------------------------------------------
# mcp: register typed tools (§4.3: MCP = адаптер к gateway, не обходной канал)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class McpToolDefinition:
    """Инструмент MCP привязан к ОДНОЙ типизированной операции gateway.

    Нет ``handler``/``command``/``script``: регистрация не выполняет код.
    """

    tool_name: str
    tool_version: str
    capability: CapabilityName
    operation: str
    input_schema_digest: str

    def __post_init__(self) -> None:
        _require_text(self.tool_name, "tool_name")
        _require_text(self.tool_version, "tool_version")
        if not isinstance(self.capability, CapabilityName):
            raise TypedContractError("capability must be a CapabilityName")
        _require_text(self.operation, "operation")
        if OPERATION_TO_CAPABILITY.get(self.operation) != self.capability.value:
            raise TypedContractError("operation does not belong to declared capability")
        _require_digest(self.input_schema_digest, "input_schema_digest")


@dataclass(frozen=True, slots=True, kw_only=True)
class McpRegisterRequest:
    server_id: str
    tools: tuple[McpToolDefinition, ...]
    envelope: CapabilityEnvelope

    def __post_init__(self) -> None:
        _require_text(self.server_id, "server_id")
        if not isinstance(self.tools, tuple) or not self.tools:
            raise TypedContractError("tools must be a non-empty tuple")
        if any(not isinstance(item, McpToolDefinition) for item in self.tools):
            raise TypedContractError("tools must contain McpToolDefinition values")
        names = [item.tool_name for item in self.tools]
        if len(set(names)) != len(names):
            raise TypedContractError("tool names must be unique")


@dataclass(frozen=True, slots=True, kw_only=True)
class McpRegisterResponse(TypedResponse):
    server_id: str = ""
    registered: int = 0

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_text(self.server_id, "server_id")
        _require_non_negative(self.registered, "registered")


# ---------------------------------------------------------------------------
# secrets: read по secret_ref (§4.3). Plaintext НЕ возвращается контрактом.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SecretsReadRequest:
    secret_ref: str
    envelope: CapabilityEnvelope

    def __post_init__(self) -> None:
        _require_text(self.secret_ref, "secret_ref")


@dataclass(frozen=True, slots=True, kw_only=True)
class SecretsReadResponse(TypedResponse):
    """Возвращает ССЫЛКУ и digest версии, но никогда plaintext/токен.

    Фактическое значение адаптер отдаёт в защищённый runtime плагина вне
    контракта аудита; ``value``/``secret``/``token`` полей здесь нет.
    """

    secret_ref: str = ""
    version: str = ""
    value_digest: str = field(default_factory=empty_digest)

    def __post_init__(self) -> None:
        super().__post_init__()
        _require_text(self.secret_ref, "secret_ref")
        _require_text(self.version, "version")
        _require_digest(self.value_digest, "value_digest")


# ---------------------------------------------------------------------------
# network: egress request (§4.3, §6). Управляется grant'ом в БД, не JWT (кр.22).
# ---------------------------------------------------------------------------


class NetworkHttpMethod(StrEnum):
    GET = "GET"
    HEAD = "HEAD"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


@dataclass(frozen=True, slots=True, kw_only=True)
class NetworkEgressRequest:
    """Типизированный egress: хост/порт/путь/метод + digest тела.

    Список ``allowed_hosts`` НЕ передаётся плагином и НЕ читается из JWT:
    политика разрешается сервером из ``CapabilityGrant`` (§13.3, кр. 22).
    Таймаут задаётся сервером, поля ``timeout_ms`` здесь нет (§4.4).
    """

    host: str
    port: int
    path: str
    method: NetworkHttpMethod
    body_digest: str = field(default_factory=empty_digest)
    envelope: CapabilityEnvelope

    def __post_init__(self) -> None:
        _require_text(self.host, "host")
        _require_positive(self.port, "port")
        if self.port > 65535:
            raise TypedContractError("port must be <= 65535")
        _require_text(self.path, "path")
        if not self.path.startswith("/"):
            raise TypedContractError("path must start with '/'")
        if not isinstance(self.method, NetworkHttpMethod):
            raise TypedContractError("method must be a NetworkHttpMethod")
        _require_digest(self.body_digest, "body_digest")


@dataclass(frozen=True, slots=True, kw_only=True)
class NetworkEgressResponse(TypedResponse):
    status_code: int = 0
    response_digest: str = field(default_factory=empty_digest)
    bytes_sent: int = 0
    bytes_received: int = 0

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.status_code, int) or not (100 <= self.status_code <= 599):
            raise TypedContractError("status_code must be a valid HTTP status")
        _require_digest(self.response_digest, "response_digest")
        _require_non_negative(self.bytes_sent, "bytes_sent")
        _require_non_negative(self.bytes_received, "bytes_received")


# ---------------------------------------------------------------------------
# Единый discriminated union запросов: сервис принимает ТОЛЬКО эти типы.
# ---------------------------------------------------------------------------

PluginCapabilityRequest = (
    CacheGetRequest | CacheSetRequest | CacheDeleteRequest | CacheIncrRequest | CachePublishRequest
    | ObjectsScopedCredentialsRequest | ObjectsListRequest | ObjectsDeleteRequest
    | DocsQueryRequest
    | EventsPublishRequest | EventsSubscribeRequest
    | JobsSubmitRequest | JobsGetRequest | JobsCancelRequest
    | PodsInvokeRequest
    | McpRegisterRequest
    | SecretsReadRequest
    | NetworkEgressRequest
)


__all__ = [
    "AdapterName",
    "CacheDeleteRequest",
    "CacheDeleteResponse",
    "CacheGetRequest",
    "CacheGetResponse",
    "CacheIncrRequest",
    "CacheIncrResponse",
    "CachePublishRequest",
    "CachePublishResponse",
    "CacheSetRequest",
    "CacheSetResponse",
    "CapabilityEnvelope",
    "CapabilityName",
    "DB_DIRECT",
    "DocsPredicate",
    "DocsQueryOperator",
    "DocsQueryRequest",
    "DocsQueryResponse",
    "DocsSortOrder",
    "DocumentRef",
    "EventsPublishRequest",
    "EventsPublishResponse",
    "EventsStartOffset",
    "EventsSubscribeRequest",
    "EventsSubscribeResponse",
    "JobLease",
    "JobState",
    "JobsCancelRequest",
    "JobsCancelResponse",
    "JobsGetRequest",
    "JobsGetResponse",
    "JobsSubmitRequest",
    "JobsSubmitResponse",
    "McpRegisterRequest",
    "McpRegisterResponse",
    "McpToolDefinition",
    "NetworkEgressRequest",
    "NetworkEgressResponse",
    "NetworkHttpMethod",
    "ObjectPermission",
    "ObjectRef",
    "ObjectsDeleteRequest",
    "ObjectsDeleteResponse",
    "ObjectsListRequest",
    "ObjectsListResponse",
    "ObjectsScopedCredentialsRequest",
    "ObjectsScopedCredentialsResponse",
    "OPERATION_TO_CAPABILITY",
    "PLUGIN_CAPABILITIES",
    "PluginCapabilityRequest",
    "PodActionName",
    "PodsInvokeRequest",
    "PodsInvokeResponse",
    "PresignedObject",
    "ScopedCredentials",
    "SecretsReadRequest",
    "SecretsReadResponse",
    "TypedContractError",
    "TypedResponse",
    "empty_digest",
]
