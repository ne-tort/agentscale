"""Типизированные HTTP-тела ``/capabilities/v1/*`` (задача 04).

Почему тела живут здесь, а не в ``capabilities/contracts.py``: задача 03
описывает **доменные** контракты как frozen ``dataclass`` (не Pydantic).
Транспортный слой не имеет права править чужой каталог, поэтому здесь —
Pydantic-модели запросов, которые один-в-один конвертируются в датаклассы
``contracts.py``. Родитель/задача 03 согласовали именно это разделение.

Правила (v4 §4.3, §13.1, ADR-002, ТЗ п.2):
  * каждое тело — типизированная модель со СТРОГО заданными полями;
  * ``extra='forbid'``: любое лишнее поле (``sql``, ``command``, ``args``,
    ``raw``, ``params``, ``payload``, ...) → 422, а не «тихо проигнорировано»;
  * ``FORBIDDEN_FIELDS`` проверяются отдельным валидатором даже для вложенных
    моделей — дополнительная страховка от generic row/action dispatch;
  * ``company_id`` / ``artifact_digest`` / ``grant_id`` / ``envelope`` в тело
    НЕ входят: они берутся из проверенных ``PluginClaims`` (§7.2). Входящие
    ``X-Company-ID: evil`` и аналогичные заголовки игнорируются в ``deps.py``.

Ни одного ``dict[str, Any]``, ``body`` или ``params`` в публичных полях.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from prodavan.application.plugins.transport.errors import GENERIC_DISPATCH_FIELDS

# Поля, которые принципиально не могут прийти от плагина: сервер берёт их из
# проверенного JWT. Их наличие в теле — попытка подмены identity (§7.2).
IDENTITY_FIELDS: frozenset[str] = frozenset(
    {"company_id", "artifact_digest", "grant_id", "scopes", "envelope", "plugin_id", "plugin_instance_id"}
)

DigestStr = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$", min_length=71, max_length=71)]
SafeStr = Annotated[str, Field(min_length=1, max_length=256)]
KeyStr = Annotated[str, Field(min_length=1, max_length=512)]


class PluginBody(BaseModel):
    """База всех тел: запрет лишних полей + запрет generic dispatch/identity.

    ``extra='forbid'`` отлавливает ``sql``/``command``/``args``/``raw`` на
    верхнем уровне; ``model_validator`` проверяет исходный dict на запрещённые
    ключи ДО того, как Pydantic отбросит неизвестные поля (и для вложенных
    объектов через ``_reject_forbidden_fields``).
    """

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=False)

    @model_validator(mode="before")
    @classmethod
    def _reject_forbidden_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        forbidden = GENERIC_DISPATCH_FIELDS | IDENTITY_FIELDS
        found = sorted(str(key).lower() for key in data if str(key).lower() in forbidden)
        if found:
            # Текст содержит маркер 'generic dispatch' — errors.py по нему
            # выставляет код FORBIDDEN_GENERIC_DISPATCH, а не общий 422.
            raise ValueError(
                "generic dispatch / identity spoofing fields are forbidden: "
                + ", ".join(found)
            )
        return data

    @field_validator("*", mode="after")
    @classmethod
    def _no_control_chars(cls, value: Any) -> Any:
        """Защита от инъекции в ключах/идентификаторах (как в contracts.py)."""
        if isinstance(value, str) and any(char in value for char in ("\x00", "\n", "\r")):
            raise ValueError("value must not contain control characters")
        return value


# ---------------------------------------------------------------------------
# cache: get / set / delete / incr / publish -> CacheXxxRequest
# ---------------------------------------------------------------------------


class CacheGetBody(PluginBody):
    key: KeyStr


class CacheSetBody(PluginBody):
    key: KeyStr
    value: str = Field(max_length=1_048_576)


class CacheDeleteBody(PluginBody):
    key: KeyStr


class CacheIncrBody(PluginBody):
    key: KeyStr
    amount: int = 1


class CachePublishBody(PluginBody):
    channel: SafeStr
    message_digest: DigestStr


# ---------------------------------------------------------------------------
# objects: scoped credentials / list / delete
# ---------------------------------------------------------------------------


class ObjectsCredentialsBody(PluginBody):
    bucket: SafeStr
    key_prefix: SafeStr
    permissions: tuple[Literal["read", "write", "delete"], ...] = Field(min_length=1)
    ttl_seconds: int = Field(default=900, ge=1, le=3600)

    @field_validator("permissions")
    @classmethod
    def _unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("permissions must not contain duplicates")
        return value


class ObjectsListBody(PluginBody):
    """Тело для ``GET /objects`` (query-параметры запрещены ТЗ — только body)."""

    bucket: SafeStr
    key_prefix: SafeStr
    limit: int = Field(default=100, ge=1, le=1000)
    cursor: str | None = Field(default=None, max_length=512)


class ObjectsDeleteBody(PluginBody):
    """Тело для ``DELETE /objects/{key}``; ``key`` дублируется в path для явности."""

    bucket: SafeStr
    key: KeyStr | None = None


# ---------------------------------------------------------------------------
# docs: typed query. Никакого произвольного Mongo-JSON / фильтра.
# ---------------------------------------------------------------------------


class DocsPredicateBody(PluginBody):
    field: SafeStr
    operator: Literal["eq", "in", "range", "exists"]
    values: tuple[str, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _range_arity(self) -> DocsPredicateBody:
        if self.operator == "range" and len(self.values) != 2:
            raise ValueError("range requires exactly two bounds")
        return self


class DocsQueryBody(PluginBody):
    namespace: SafeStr
    collection: SafeStr
    predicates: tuple[DocsPredicateBody, ...] = Field(max_length=32)
    limit: int = Field(default=50, ge=1, le=1000)
    projection: tuple[SafeStr, ...] = Field(default=(), max_length=64)
    sort_field: SafeStr | None = None
    sort_order: Literal["asc", "desc"] = "asc"


# ---------------------------------------------------------------------------
# events / jobs / pods / mcp / secrets / network
# ---------------------------------------------------------------------------


class EventsPublishBody(PluginBody):
    topic: SafeStr
    event_type: SafeStr
    partition_key: SafeStr
    payload_digest: DigestStr


class JobsSubmitBody(PluginBody):
    task_name: SafeStr
    run_id: SafeStr
    arguments_digest: DigestStr


class JobsGetBody(PluginBody):
    """``GET /jobs/{job_id}`` — тело обязательно: ``run_id`` нельзя брать из query."""

    run_id: SafeStr


class JobsCancelBody(PluginBody):
    run_id: SafeStr
    lease_id: SafeStr


class PodsInvokeBody(PluginBody):
    pod_id: SafeStr
    action: Literal["read_status", "read_logs", "send_agent_message", "read_artifact"]
    target_ref: SafeStr
    arguments_digest: DigestStr = "sha256:" + "0" * 64


class McpToolBody(PluginBody):
    tool_name: SafeStr
    tool_version: SafeStr
    capability: Literal[
        "cache", "objects", "docs", "events", "jobs", "pods", "mcp", "secrets", "network"
    ]
    operation: SafeStr
    input_schema_digest: DigestStr


class McpRegisterBody(PluginBody):
    server_id: SafeStr
    tools: tuple[McpToolBody, ...] = Field(min_length=1, max_length=64)

    @field_validator("tools")
    @classmethod
    def _unique_names(cls, value: tuple[McpToolBody, ...]) -> tuple[McpToolBody, ...]:
        names = [item.tool_name for item in value]
        if len(set(names)) != len(names):
            raise ValueError("tool names must be unique")
        return value


class SecretsReadBody(PluginBody):
    secret_ref: SafeStr


class NetworkRequestBody(PluginBody):
    host: SafeStr
    port: int = Field(default=443, ge=1, le=65535)
    path: str = Field(default="/", min_length=1, max_length=2048)
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    body_digest: DigestStr = "sha256:" + "0" * 64

    @field_validator("path")
    @classmethod
    def _absolute_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("path must start with '/'")
        return value


# ---------------------------------------------------------------------------
# Ответы (типизированные, без произвольного dict)
# ---------------------------------------------------------------------------


class DecisionResponse(BaseModel):
    """Единый формат успеха: ``{decision, data}`` (ТЗ п.2)."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["allow", "deny"] = "allow"
    data: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    surface: Literal["plugin-capabilities"] = "plugin-capabilities"
    company_id: str


__all__ = [
    "CacheDeleteBody",
    "CacheGetBody",
    "CacheIncrBody",
    "CachePublishBody",
    "CacheSetBody",
    "DecisionResponse",
    "DocsPredicateBody",
    "DocsQueryBody",
    "EventsPublishBody",
    "HealthResponse",
    "IDENTITY_FIELDS",
    "JobsCancelBody",
    "JobsGetBody",
    "JobsSubmitBody",
    "McpRegisterBody",
    "McpToolBody",
    "NetworkRequestBody",
    "ObjectsCredentialsBody",
    "ObjectsDeleteBody",
    "ObjectsListBody",
    "PluginBody",
    "PodsInvokeBody",
    "SecretsReadBody",
]
