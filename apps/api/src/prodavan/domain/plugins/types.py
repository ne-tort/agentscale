"""Доменные типы плагинов (F0).

Только контракты: без persistence, транспорта и криптографии. Источник истины —
``docs/ARCHITECTURE/plugin-architecture-complete.md`` (v4), §2, §4.2, §5.1, §6.1.

Зеркало контрактов:
  * ``packages/schemas/plugin-manifest/v1/schema.json``
  * ``packages/schemas/plugin-manifest/v1/protocol.json``
  * ``packages/schemas/plugin-manifest/v1/types.ts``

Fail-closed: отсутствие grant/квоты/адаптера фиксируется типизированным
:class:`DecisionCode`, а не молчаливым успехом.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import json
import math
import re
from typing import Mapping


class PluginInstanceStatus(StrEnum):
    """Статус экземпляра плагина (доменный, не статус Kubernetes Pod)."""

    INSTALLED = "installed"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    QUARANTINED = "quarantined"


class Capability(StrEnum):
    """Разрешённые capability gateway.

    ``db.direct`` и ``db.migrations`` намеренно отсутствуют: БД плагина доступна
    по прямому DSN к ``tenant_<company>`` (§2 v4), остальная инфраструктура —
    через типизированные адаптеры. Плагин не знает реализации адаптера.
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


CAPABILITY_ALLOWLIST: frozenset[str] = frozenset(item.value for item in Capability)
"""Явный allowlist capability (совпадает с enum schema.json)."""


class MigrationEngine(StrEnum):
    """Движок миграций схемы плагина (changelog внутри OCI-артефакта)."""

    LIQUIBASE = "liquibase"
    FLYWAY = "flyway"
    ALEMBIC = "alembic"


class EgressMode(StrEnum):
    """Режим egress-политики (§6.3 v4). Источник правил — grant в БД."""

    NONE = "none"
    WEBHOOKS_ONLY = "webhooks-only"
    FULL = "full"


class RegistryOutcome(StrEnum):
    """Исход регистрации версии в реестре (AppVersion/RegistryOutcome, §5.1 v4)."""

    APPLIED = "applied"
    CONFLICT = "conflict"
    QUARANTINED = "quarantined"
    MISSING = "missing"


class DecisionCode(StrEnum):
    """Коды решений gateway плагинов.

    Переиспользует идею ``application/capability_gateway_service.py::DecisionCode``,
    но это самостоятельный enum домена плагинов: agent-поверхность :8001 и trust
    ``pod_bridge`` не смешиваются с ``/capabilities/v1/*`` (§4.1 v4).
    """

    ALLOWED = "allowed"
    INVALID_REQUEST = "invalid_request"
    CAPABILITY_UNKNOWN = "capability_unknown"
    CAPABILITY_NOT_REQUESTED = "capability_not_requested"
    IDENTITY_DENIED = "identity_denied"
    SCOPE_DENIED = "scope_denied"
    GRANT_MISSING = "grant_missing"
    GRANT_NOT_YET_ISSUED = "grant_not_yet_issued"
    GRANT_EXPIRED = "grant_expired"
    GRANT_REVOKED = "grant_revoked"
    QUOTA_EXCEEDED = "quota_exceeded"
    ADAPTER_MISSING = "adapter_missing"
    ADAPTER_FAILED = "adapter_failed"


PLUGIN_ID_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]*$")
SEMVER_PATTERN: re.Pattern[str] = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)
ARTIFACT_DIGEST_PATTERN: re.Pattern[str] = re.compile(r"^sha256:[0-9a-f]{64}$")
EGRESS_REF_PATTERN: re.Pattern[str] = re.compile(r"^grant:[A-Za-z0-9_.:/-]+$")
PATH_PATTERN: re.Pattern[str] = re.compile(r"^/[A-Za-z0-9._/-]*$")
# Public pages allow exactly '/', but reject empty path segments and keep the
# same traversal/URI restrictions as other absolute plugin paths.
PUBLIC_PAGE_PATTERN: re.Pattern[str] = re.compile(r"^(?!.*//)/[A-Za-z0-9._/-]*$")
RELATIVE_PATH_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9._/-]+$")
PROTOCOL_VERSION: str = "plugin-rpc/1"
PLUGIN_CAPABILITY_ID_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_-]{1,63}(\.[a-z][a-z0-9_-]{1,63})?$")
PLUGIN_CAPABILITY_VERSION_PATTERN: re.Pattern[str] = re.compile(r"^[0-9]+\.[0-9]+$")
PLUGIN_OPERATION_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
PLUGIN_RPC_METHODS: frozenset[str] = frozenset({"GET", "POST"})
MCP_SERVER_ID_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
MCP_TOOL_NAME_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
POD_SCOPE_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
POD_MAX_ALLOWLIST = 256
POD_SCOPE_MAX_LENGTH = 256
SECRET_DELIVERY_MODE = "k8s_secret"
SECRET_DELIVERY_MAX_ALLOWLIST = 256
SECRET_REF_MAX_LENGTH = 512
SECRET_REF_PATTERN: re.Pattern[str] = re.compile(
    r"^(?:vault|file)://[A-Za-z0-9][A-Za-z0-9._-]*"
    r"(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$"
)
MCP_PROPERTY_NAME_PATTERN: re.Pattern[str] = MCP_TOOL_NAME_PATTERN
MCP_SCHEMA_TYPES: frozenset[str] = frozenset(
    {"object", "array", "string", "number", "integer", "boolean", "null"}
)
MCP_SCHEMA_FIELDS: frozenset[str] = frozenset({
    "type", "title", "description", "properties", "required", "additionalProperties",
    "items", "minProperties", "maxProperties", "minItems", "maxItems", "uniqueItems",
    "minLength", "maxLength", "pattern", "minimum", "maximum", "exclusiveMinimum",
    "exclusiveMaximum", "enum", "const",
})
MCP_FORBIDDEN_DECLARATION_FIELDS: frozenset[str] = frozenset({
    "command", "script", "handler", "executable", "entrypoint", "endpoint", "url", "uri",
    "env", "secret", "secrets", "token", "access_token", "session_token", "webhook_secret",
    "password", "api_key", "private_key", "credentials", "authorization", "dsn",
})
MCP_UNSAFE_TEXT_PATTERN: re.Pattern[str] = re.compile(
    r"(?:[a-z][a-z0-9+.-]*://|\bwww\.|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b(?:password|secret|token|api_key|private_key|dsn)\s*[:=])",
    re.IGNORECASE,
)
MCP_MAX_SERVERS = 256
MCP_MAX_TOOLS_PER_SERVER = 64
MCP_MAX_DESCRIPTION_LENGTH = 1024
MCP_MAX_SCHEMA_PROPERTIES = 64
MCP_MAX_SCHEMA_DEPTH = 8
MCP_MAX_JSON_DEPTH = 16
MCP_MAX_SCHEMA_NODES = 1024
MCP_MAX_SCHEMA_BYTES = 16 * 1024


@dataclass(frozen=True, slots=True)
class RpcOperationDeclaration:
    """Одна объявленная plugin RPC-операция (маршрут broker → plugin backend).

    ``capability`` — типизированный id/version, а НЕ свободная строка: broker и
    bootstrap доверяют только операциям из подписанного manifest. ``public``
    разрешает вызов только для route_path из ``public_pages``; приватный UI видит
    все операции.
    """

    capability_id: str
    capability_version: str
    operation: str
    http_method: str
    backend_path: str
    public: bool = False

    @property
    def capability(self) -> str:
        return f"{self.capability_id}@{self.capability_version}"

    def __post_init__(self) -> None:
        if not isinstance(self.capability_id, str) or not PLUGIN_CAPABILITY_ID_PATTERN.fullmatch(self.capability_id):
            raise ValueError("rpc operation capability id is invalid")
        if not isinstance(self.capability_version, str) or not PLUGIN_CAPABILITY_VERSION_PATTERN.fullmatch(self.capability_version):
            raise ValueError("rpc operation capability version must be '<major>.<minor>'")
        if not isinstance(self.operation, str) or not PLUGIN_OPERATION_PATTERN.fullmatch(self.operation):
            raise ValueError("rpc operation name is invalid")
        if not isinstance(self.http_method, str) or self.http_method not in PLUGIN_RPC_METHODS:
            raise ValueError("rpc operation http method must be GET or POST")
        _validate_absolute_path(self.backend_path, field_name="rpc_operations.backend_path")
        if not self.backend_path.startswith("/api/"):
            raise ValueError("rpc operation backend_path must stay under /api/")
        if not isinstance(self.public, bool):
            raise ValueError("rpc operation public must be a boolean")


@dataclass(frozen=True, slots=True)
class PluginRpcOperations:
    """Неизменяемый реестр операций; дубликаты запрещены fail-closed."""

    operations: tuple[RpcOperationDeclaration, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.operations, tuple):
            raise ValueError("rpc operations must be a tuple")
        keys = [
            (item.capability_id, item.capability_version, item.operation)
            for item in self.operations
        ]
        if len(set(keys)) != len(keys):
            raise ValueError("rpc operations must not contain duplicate capability/operation")
        paths = [item.backend_path for item in self.operations]
        if len(set(paths)) != len(paths):
            raise ValueError("rpc operations must not reuse the same backend_path")

    def resolve(
        self,
        *,
        capability_id: str,
        capability_version: str,
        operation: str,
        public: bool,
    ) -> RpcOperationDeclaration | None:
        for item in self.operations:
            if (
                item.capability_id == capability_id
                and item.capability_version == capability_version
                and item.operation == operation
            ):
                if public and not item.public:
                    return None
                return item
        return None

    def public_operations(self) -> tuple[RpcOperationDeclaration, ...]:
        return tuple(item for item in self.operations if item.public)

SELF_REFERENCE_FIELDS: frozenset[str] = frozenset({"artifact_digest", "signature"})
"""Поля, запрещённые в манифесте: подписываемый документ не может нести свой digest/подпись."""

EMBEDDED_EGRESS_FIELDS: frozenset[str] = frozenset(
    {"egress", "allowed_hosts", "allowed_paths", "egress_policy"}
)
"""Встроенный allowlist запрещён: правила egress живут в CapabilityGrant в БД (§6.1 v4)."""


def _validate_semver(value: str, *, field_name: str) -> None:
    """Проверка SemVer 2.0.0 (без внешних зависимостей)."""
    if not isinstance(value, str) or not SEMVER_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must be a semantic version")


def _validate_relative_path(value: str, *, field_name: str) -> None:
    """Относительный путь внутри артефакта без выхода наверх."""
    if not isinstance(value, str) or not RELATIVE_PATH_PATTERN.fullmatch(value) or ".." in value:
        raise ValueError(f"{field_name} must be a relative path without '..'")


def _validate_absolute_path(value: str, *, field_name: str) -> None:
    """Абсолютный URL-путь без выхода наверх."""
    if not isinstance(value, str) or not PATH_PATTERN.fullmatch(value) or ".." in value:
        raise ValueError(f"{field_name} must be an absolute path without '..'")


def _validate_mcp_text(value: object, *, field_name: str, max_length: int) -> None:
    if (
        not isinstance(value, str)
        or len(value) > max_length
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or MCP_UNSAFE_TEXT_PATTERN.search(value)
    ):
        raise ValueError(f"{field_name} contains unsafe or oversized text")


def _validate_mcp_scalar(value: object, *, field_name: str) -> None:
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} must contain finite JSON numbers")
        return
    if isinstance(value, str):
        _validate_mcp_text(value, field_name=field_name, max_length=1024)
        return
    raise ValueError(f"{field_name} must contain only scalar JSON values")


def _strict_mcp_json(value: object) -> object:
    """Скопировать JSON-only schema с теми же resource bounds, что discovery."""
    nodes = 0

    def walk(item: object, depth: int) -> object:
        nonlocal nodes
        nodes += 1
        if nodes > MCP_MAX_SCHEMA_NODES or depth > MCP_MAX_JSON_DEPTH:
            raise ValueError("mcp input_schema exceeds JSON depth/node limits")
        if item is None or isinstance(item, (str, bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("mcp input_schema numbers must be finite")
            return item
        if isinstance(item, Mapping):
            result: dict[str, object] = {}
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError("mcp input_schema keys must be strings")
                result[key] = walk(child, depth + 1)
            return result
        if isinstance(item, (list, tuple)):
            return [walk(child, depth + 1) for child in item]
        raise ValueError("mcp input_schema must contain JSON values only")

    return walk(value, 0)


def _validate_mcp_schema_node(value: object, *, depth: int, field_name: str) -> None:
    if depth >= MCP_MAX_SCHEMA_DEPTH or not isinstance(value, Mapping):
        raise ValueError(f"{field_name} exceeds schema depth limits")
    unknown = set(value) - MCP_SCHEMA_FIELDS
    if unknown:
        raise ValueError(f"{field_name} contains unsupported or executable schema fields")
    schema_type = value.get("type")
    if not isinstance(schema_type, str) or schema_type not in MCP_SCHEMA_TYPES:
        raise ValueError(f"{field_name}.type is invalid")

    for key, maximum in (
        ("minLength", 4096), ("maxLength", 4096),
        ("minItems", 1024), ("maxItems", 1024),
        ("minProperties", 64), ("maxProperties", 64),
    ):
        if key in value and (
            not isinstance(value[key], int) or isinstance(value[key], bool)
            or not 0 <= value[key] <= maximum
        ):
            raise ValueError(f"{field_name}.{key} is outside its bounds")
    for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        if key in value and (
            not isinstance(value[key], (int, float)) or isinstance(value[key], bool)
            or (isinstance(value[key], float) and not math.isfinite(value[key]))
        ):
            raise ValueError(f"{field_name}.{key} must be a finite number")
    for key in ("additionalProperties", "uniqueItems"):
        if key in value and not isinstance(value[key], bool):
            raise ValueError(f"{field_name}.{key} must be boolean")
    for key, maximum in (("title", 128), ("description", 512), ("pattern", 256)):
        if key in value:
            _validate_mcp_text(value[key], field_name=f"{field_name}.{key}", max_length=maximum)

    required = value.get("required")
    if required is not None:
        if not isinstance(required, list) or len(required) > MCP_MAX_SCHEMA_PROPERTIES:
            raise ValueError(f"{field_name}.required exceeds its bounds")
        if any(
            not isinstance(item, str) or not MCP_PROPERTY_NAME_PATTERN.fullmatch(item)
            or item in MCP_FORBIDDEN_DECLARATION_FIELDS
            for item in required
        ) or len(set(required)) != len(required):
            raise ValueError(f"{field_name}.required contains invalid or duplicate names")

    properties = value.get("properties")
    if properties is not None:
        if not isinstance(properties, Mapping) or len(properties) > MCP_MAX_SCHEMA_PROPERTIES:
            raise ValueError(f"{field_name}.properties exceeds its bounds")
        for name, child in properties.items():
            if (
                not MCP_PROPERTY_NAME_PATTERN.fullmatch(name)
                or name in MCP_FORBIDDEN_DECLARATION_FIELDS
            ):
                raise ValueError(f"{field_name}.properties contains a forbidden name")
            _validate_mcp_schema_node(child, depth=depth + 1, field_name=f"{field_name}.properties")
    if "items" in value:
        _validate_mcp_schema_node(value["items"], depth=depth + 1, field_name=f"{field_name}.items")

    enum = value.get("enum")
    if enum is not None:
        if not isinstance(enum, list) or not 1 <= len(enum) <= 64:
            raise ValueError(f"{field_name}.enum exceeds its bounds")
        for item in enum:
            _validate_mcp_scalar(item, field_name=f"{field_name}.enum")
        if len({json.dumps(item, sort_keys=True) for item in enum}) != len(enum):
            raise ValueError(f"{field_name}.enum must not contain duplicates")
    if "const" in value:
        _validate_mcp_scalar(value["const"], field_name=f"{field_name}.const")


def _validate_mcp_input_schema(value: object) -> None:
    if not isinstance(value, Mapping) or value.get("type") != "object":
        raise ValueError("mcp input_schema must be an object schema")
    normalized = _strict_mcp_json(value)
    try:
        encoded = json.dumps(
            normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise ValueError("mcp input_schema is not canonicalizable JSON") from None
    if len(encoded) > MCP_MAX_SCHEMA_BYTES:
        raise ValueError("mcp input_schema exceeds its byte limit")
    _validate_mcp_schema_node(normalized, depth=0, field_name="mcp input_schema")


@dataclass(frozen=True, slots=True)
class FrontendEntrypoint:
    """Точка входа frontend на отдельном static-origin (§5.2 v4)."""

    entry: str
    base_path: str

    def __post_init__(self) -> None:
        _validate_relative_path(self.entry, field_name="frontend.entry")
        _validate_absolute_path(self.base_path, field_name="frontend.base_path")


@dataclass(frozen=True, slots=True)
class BackendEntrypoint:
    """Backend контейнера плагина; /health Ready только после apply миграций."""

    port: int
    health: str

    def __post_init__(self) -> None:
        if not isinstance(self.port, int) or isinstance(self.port, bool) or not 1 <= self.port <= 65535:
            raise ValueError("backend.port must be an integer in 1..65535")
        _validate_absolute_path(self.health, field_name="backend.health")


@dataclass(frozen=True, slots=True)
class MigrationsDeclaration:
    """Объявление changelog плагина; SQL применяется самим плагином под своей ролью."""

    engine: MigrationEngine
    path: str

    def __post_init__(self) -> None:
        if not isinstance(self.engine, MigrationEngine):
            raise ValueError("migrations.engine must be a MigrationEngine")
        _validate_relative_path(self.path, field_name="migrations.path")


@dataclass(frozen=True, slots=True)
class McpToolDeclaration:
    """Data-only MCP tool из подписанного manifest, без handler/endpoint/secrets."""

    name: str
    description: str
    version: str
    input_schema: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not MCP_TOOL_NAME_PATTERN.fullmatch(self.name):
            raise ValueError("mcp tool name is invalid")
        if (
            not isinstance(self.description, str) or not self.description
            or self.description != self.description.strip()
        ):
            raise ValueError("mcp tool description must be non-empty and trimmed")
        _validate_mcp_text(
            self.description, field_name="mcp tool description", max_length=MCP_MAX_DESCRIPTION_LENGTH,
        )
        _validate_semver(self.version, field_name="mcp tool version")
        _validate_mcp_input_schema(self.input_schema)


@dataclass(frozen=True, slots=True)
class McpServerDeclaration:
    """Логический server_id и уникальные tool declarations, не runtime transport."""

    server_id: str
    tools: tuple[McpToolDeclaration, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.server_id, str) or not MCP_SERVER_ID_PATTERN.fullmatch(self.server_id):
            raise ValueError("mcp server_id is invalid")
        if (
            not isinstance(self.tools, tuple)
            or not 1 <= len(self.tools) <= MCP_MAX_TOOLS_PER_SERVER
            or any(not isinstance(item, McpToolDeclaration) for item in self.tools)
        ):
            raise ValueError("mcp server tools must be a bounded non-empty tuple")
        names = [item.name for item in self.tools]
        if len(set(names)) != len(names):
            raise ValueError("mcp tool names must be unique per server")


@dataclass(frozen=True, slots=True)
class PodAllowlistEntry:
    """Exact instance/project/job scope без pod id, wildcard, command или args."""

    plugin_instance_id: str
    project_id: str
    job_id: str

    def __post_init__(self) -> None:
        for field_name in ("plugin_instance_id", "project_id", "job_id"):
            value = getattr(self, field_name)
            if (
                not isinstance(value, str)
                or len(value) > POD_SCOPE_MAX_LENGTH
                or not POD_SCOPE_PATTERN.fullmatch(value)
            ):
                raise ValueError(f"pods.allowlist.{field_name} must be an exact bounded identifier")


@dataclass(frozen=True, slots=True)
class PodsDeclaration:
    """Signed non-empty exact allowlist; omitted declaration grants no pod access."""

    allowlist: tuple[PodAllowlistEntry, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.allowlist, tuple)
            or not 1 <= len(self.allowlist) <= POD_MAX_ALLOWLIST
            or any(not isinstance(item, PodAllowlistEntry) for item in self.allowlist)
        ):
            raise ValueError("pods.allowlist must contain 1..256 typed entries")
        keys = [
            (item.plugin_instance_id, item.project_id, item.job_id)
            for item in self.allowlist
        ]
        if len(set(keys)) != len(keys):
            raise ValueError("pods.allowlist must not contain duplicate entries")


@dataclass(frozen=True, slots=True)
class SecretDeliveryDeclaration:
    """References only: no delivery Secret names/data и no plaintext values."""

    mode: str
    allowlist: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.mode, str) or self.mode != SECRET_DELIVERY_MODE:
            raise ValueError("secret_delivery.mode must be k8s_secret")
        if (
            not isinstance(self.allowlist, tuple)
            or not 1 <= len(self.allowlist) <= SECRET_DELIVERY_MAX_ALLOWLIST
        ):
            raise ValueError("secret_delivery.allowlist must contain 1..256 secret refs")
        for ref in self.allowlist:
            if (
                not isinstance(ref, str)
                or len(ref) > SECRET_REF_MAX_LENGTH
                or not SECRET_REF_PATTERN.fullmatch(ref)
            ):
                raise ValueError("secret_delivery.allowlist must contain canonical vault:// or file:// refs")
        if len(set(self.allowlist)) != len(self.allowlist):
            raise ValueError("secret_delivery.allowlist must not contain duplicate refs")


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """Манифест плагина v1 (подписан как OCI annotation).

    Не содержит ``artifact_digest``/``signature`` (самореференция) и не содержит
    встроенного egress allowlist — только ``egress_ref`` на grant в БД.
    """

    plugin_id: str
    version: str
    protocol: str
    frontend: FrontendEntrypoint
    backend: BackendEntrypoint
    capabilities: tuple[Capability, ...]
    migrations: MigrationsDeclaration
    egress_ref: str
    public_pages: tuple[str, ...]
    webhooks: tuple[str, ...]
    min_platform_version: str
    rpc_operations: PluginRpcOperations = PluginRpcOperations()
    mcp_servers: tuple[McpServerDeclaration, ...] = ()
    pods: PodsDeclaration | None = None
    secret_delivery: SecretDeliveryDeclaration | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.plugin_id, str) or not PLUGIN_ID_PATTERN.fullmatch(self.plugin_id):
            raise ValueError("plugin_id must match ^[a-z][a-z0-9_]*$")
        _validate_semver(self.version, field_name="version")
        if self.protocol != PROTOCOL_VERSION:
            raise ValueError(f"protocol must be {PROTOCOL_VERSION}")
        if not isinstance(self.frontend, FrontendEntrypoint):
            raise ValueError("frontend must be a FrontendEntrypoint")
        if not isinstance(self.backend, BackendEntrypoint):
            raise ValueError("backend must be a BackendEntrypoint")
        if not isinstance(self.capabilities, tuple):
            raise ValueError("capabilities must be a tuple")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("capabilities must not contain duplicates")
        for capability in self.capabilities:
            if not isinstance(capability, Capability):
                raise ValueError("every capability must be a Capability member")
        if not isinstance(self.migrations, MigrationsDeclaration):
            raise ValueError("migrations must be a MigrationsDeclaration")
        if not isinstance(self.egress_ref, str) or not EGRESS_REF_PATTERN.fullmatch(self.egress_ref):
            raise ValueError("egress_ref must reference a grant (grant:...)")
        for field_name, values in (("public_pages", self.public_pages), ("webhooks", self.webhooks)):
            if not isinstance(values, tuple):
                raise ValueError(f"{field_name} must be a tuple")
            if len(set(values)) != len(values):
                raise ValueError(f"{field_name} must not contain duplicates")
            for value in values:
                _validate_absolute_path(value, field_name=field_name)
        _validate_semver(self.min_platform_version, field_name="min_platform_version")
        if not isinstance(self.rpc_operations, PluginRpcOperations):
            raise ValueError("rpc_operations must be PluginRpcOperations")
        if (
            not isinstance(self.mcp_servers, tuple) or len(self.mcp_servers) > MCP_MAX_SERVERS
            or any(not isinstance(item, McpServerDeclaration) for item in self.mcp_servers)
        ):
            raise ValueError("mcp_servers must be a bounded tuple of declarations")
        server_ids = [item.server_id for item in self.mcp_servers]
        if len(set(server_ids)) != len(server_ids):
            raise ValueError("mcp server_id values must be unique")
        if self.pods is not None and not isinstance(self.pods, PodsDeclaration):
            raise ValueError("pods must be a PodsDeclaration or None")
        if self.secret_delivery is not None and not isinstance(
            self.secret_delivery, SecretDeliveryDeclaration
        ):
            raise ValueError("secret_delivery must be a SecretDeliveryDeclaration or None")

    @property
    def requested_capabilities(self) -> frozenset[str]:
        """Запрошенные capability (для compatibility gate до PUBLISHED).

        RPC-операции здесь НЕ подмешиваются: их id/version не входят в infra
        allowlist grant'а. Наличие действующего grant'а проверяется по нему,
        иначе существующий grant стал бы «недостаточным» от одного объявления.
        """
        return frozenset(item.value for item in self.capabilities)

    @property
    def rpc_capability_ids(self) -> frozenset[str]:
        return frozenset(item.capability_id for item in self.rpc_operations.operations)


@dataclass(frozen=True, slots=True)
class ArtifactDigest:
    """Digest OCI-артефакта: ``sha256:<64 hex>``.

    Вычисляется платформой из cosign/OCI и не может быть частью манифеста.
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not ARTIFACT_DIGEST_PATTERN.fullmatch(self.value):
            raise ValueError("artifact digest must match ^sha256:[0-9a-f]{64}$")

    def __str__(self) -> str:
        return self.value

    @property
    def hex(self) -> str:
        """Шестнадцатеричная часть без префикса ``sha256:``."""
        return self.value.removeprefix("sha256:")


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    """Egress-политика из grant в БД (§6.1/§6.3 v4), а не из короткого JWT."""

    mode: EgressMode
    allowed_hosts: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.mode, EgressMode):
            raise ValueError("mode must be an EgressMode")
        for field_name, values in (("allowed_hosts", self.allowed_hosts), ("allowed_paths", self.allowed_paths)):
            if not isinstance(values, tuple):
                raise ValueError(f"{field_name} must be a tuple")
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"{field_name} must contain non-empty strings")
            if len(set(values)) != len(values):
                raise ValueError(f"{field_name} must not contain duplicates")
        if self.mode is EgressMode.NONE and (self.allowed_hosts or self.allowed_paths):
            raise ValueError("mode=none forbids allowed_hosts/allowed_paths")
        if self.mode is EgressMode.WEBHOOKS_ONLY and not (self.allowed_hosts and self.allowed_paths):
            raise ValueError("mode=webhooks-only requires allowed_hosts and allowed_paths")

    def allows(self, host: str, path: str) -> bool:
        """Fail-closed проверка: любое отсутствие правила = отказ."""
        if self.mode is EgressMode.NONE:
            return False
        if self.mode is EgressMode.WEBHOOKS_ONLY:
            return host in self.allowed_hosts and path in self.allowed_paths
        return self.mode is EgressMode.FULL


@dataclass(frozen=True, slots=True)
class QuotaRef:
    """Ссылка на тариф/квоту; квоты считаются per-(company, instance, digest)."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("quota_ref must not be empty")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    """Долгоживущий grant в БД (§4.2/§5.5 v4).

    Отзыв = деактивация grant и проверка ``grant_id`` на каждом запросе, а не
    bump generation пода. Egress и квоты не кладутся в короткий JWT.
    """

    grant_id: str
    plugin_id: str
    plugin_instance_id: str
    capabilities: tuple[Capability, ...]
    egress_ref: str
    egress_policy: EgressPolicy
    quota_ref: QuotaRef
    artifact_digest: ArtifactDigest
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.grant_id, str) or not self.grant_id.strip():
            raise ValueError("grant_id must not be empty")
        if not isinstance(self.plugin_id, str) or not PLUGIN_ID_PATTERN.fullmatch(self.plugin_id):
            raise ValueError("plugin_id must match ^[a-z][a-z0-9_]*$")
        if not isinstance(self.plugin_instance_id, str) or not self.plugin_instance_id.strip():
            raise ValueError("plugin_instance_id must not be empty")
        if not isinstance(self.capabilities, tuple) or any(
            not isinstance(item, Capability) for item in self.capabilities
        ):
            raise ValueError("capabilities must be a tuple of Capability members")
        if not isinstance(self.egress_ref, str) or not EGRESS_REF_PATTERN.fullmatch(self.egress_ref):
            raise ValueError("egress_ref must reference a grant (grant:...)")
        if not isinstance(self.egress_policy, EgressPolicy):
            raise ValueError("egress_policy must be an EgressPolicy")
        if not isinstance(self.quota_ref, QuotaRef):
            raise ValueError("quota_ref must be a QuotaRef")
        if not isinstance(self.artifact_digest, ArtifactDigest):
            raise ValueError("artifact_digest must be an ArtifactDigest")
        if self.expires_at is not None and self.issued_at is not None and self.expires_at < self.issued_at:
            raise ValueError("expires_at must not precede issued_at")

    @property
    def active(self) -> bool:
        """Признак неотозванного grant (без проверки времени — её делает gateway)."""
        return self.revoked_at is None

    def grants(self, capability: Capability) -> bool:
        """Явная проверка наличия capability; отсутствие = False."""
        return capability in self.capabilities and self.active


@dataclass(frozen=True, slots=True)
class PluginInstance:
    """Экземпляр установленного плагина (company-scoped, stateless)."""

    instance_id: str
    plugin_id: str
    company_id: str
    manifest_version: str
    artifact_digest: ArtifactDigest
    status: PluginInstanceStatus
    cabinet_id: str | None = None
    project_id: str | None = None
    grant_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        for field_name in ("instance_id", "plugin_id", "company_id", "manifest_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be empty")
        if not PLUGIN_ID_PATTERN.fullmatch(self.plugin_id):
            raise ValueError("plugin_id must match ^[a-z][a-z0-9_]*$")
        _validate_semver(self.manifest_version, field_name="manifest_version")
        if not isinstance(self.artifact_digest, ArtifactDigest):
            raise ValueError("artifact_digest must be an ArtifactDigest")
        if not isinstance(self.status, PluginInstanceStatus):
            raise ValueError("status must be a PluginInstanceStatus")
        if self.updated_at is not None and self.created_at is not None and self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")


@dataclass(frozen=True, slots=True)
class PluginClaims:
    """Набор полей PluginClaims (§4.2 v4). Только типы, БЕЗ криптографии.

    Mint/verify/JWKS/revocation — зона F1. Egress-политика и квоты в токен
    не входят: token несёт ``grant_id`` и короткий ``exp``.
    """

    iss: str = "agentscale-plugins"
    aud: str = "capability-gateway"
    typ: str = "plugin_capability"
    sub: str = ""
    company_id: str = ""
    cabinet_id: str | None = None
    project_id: str | None = None
    plugin_id: str = ""
    plugin_instance_id: str = ""
    manifest_version: str = ""
    artifact_digest: str = ""
    grant_id: str = ""
    scopes: tuple[Capability, ...] = ()
    quota_ref: str = ""
    jti: str = ""
    exp: int = 0

    def __post_init__(self) -> None:
        if self.iss != "agentscale-plugins" or self.aud != "capability-gateway":
            raise ValueError("PluginClaims requires distinct plugin issuer/audience")
        if self.typ != "plugin_capability":
            raise ValueError("PluginClaims.typ must be plugin_capability")
        for field_name in ("sub", "company_id", "plugin_id", "plugin_instance_id", "grant_id", "jti"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be empty")
        if not PLUGIN_ID_PATTERN.fullmatch(self.plugin_id):
            raise ValueError("plugin_id must match ^[a-z][a-z0-9_]*$")
        _validate_semver(self.manifest_version, field_name="manifest_version")
        if not ARTIFACT_DIGEST_PATTERN.fullmatch(self.artifact_digest):
            raise ValueError("artifact_digest must match ^sha256:[0-9a-f]{64}$")
        if any(not isinstance(item, Capability) for item in self.scopes):
            raise ValueError("scopes must contain only Capability members")
        if not isinstance(self.exp, int) or isinstance(self.exp, bool) or self.exp <= 0:
            raise ValueError("exp must be a positive unix timestamp")

    def to_claims_dict(self, extra: Mapping[str, object] | None = None) -> dict[str, object]:
        """Плоский словарь claim'ов для последующего mint (без подписи)."""
        payload: dict[str, object] = {
            "iss": self.iss,
            "aud": self.aud,
            "typ": self.typ,
            "sub": self.sub,
            "company_id": self.company_id,
            "cabinet_id": self.cabinet_id,
            "project_id": self.project_id,
            "plugin_id": self.plugin_id,
            "plugin_instance_id": self.plugin_instance_id,
            "manifest_version": self.manifest_version,
            "artifact_digest": self.artifact_digest,
            "grant_id": self.grant_id,
            "scopes": [item.value for item in self.scopes],
            "quota_ref": self.quota_ref,
            "jti": self.jti,
            "exp": self.exp,
        }
        if extra:
            payload.update(extra)
        return payload


@dataclass(frozen=True, slots=True)
class RegistryResult:
    """Результат compatibility gate / публикации версии (§5.1 v4)."""

    outcome: RegistryOutcome
    artifact_digest: ArtifactDigest
    plugin_id: str
    version: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, RegistryOutcome):
            raise ValueError("outcome must be a RegistryOutcome")
        if not isinstance(self.artifact_digest, ArtifactDigest):
            raise ValueError("artifact_digest must be an ArtifactDigest")
        if not isinstance(self.plugin_id, str) or not PLUGIN_ID_PATTERN.fullmatch(self.plugin_id):
            raise ValueError("plugin_id must match ^[a-z][a-z0-9_]*$")
        _validate_semver(self.version, field_name="version")

    @property
    def published(self) -> bool:
        """Только APPLIED означает успех; всё остальное — fail-closed отказ."""
        return self.outcome is RegistryOutcome.APPLIED


@dataclass(frozen=True, slots=True)
class GatewayDecision:
    """Решение capability gateway плагинов до вызова адаптера (§4.5 v4)."""

    allowed: bool
    code: DecisionCode
    reason: str | None = None
    grant_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, DecisionCode):
            raise ValueError("code must be a DecisionCode")
        if self.allowed and self.code is not DecisionCode.ALLOWED:
            raise ValueError("allowed decision requires ALLOWED code")
        if not self.allowed and self.code is DecisionCode.ALLOWED:
            raise ValueError("denied decision must not use ALLOWED code")


__all__ = [
    "ARTIFACT_DIGEST_PATTERN",
    "CAPABILITY_ALLOWLIST",
    "EGRESS_REF_PATTERN",
    "EMBEDDED_EGRESS_FIELDS",
    "PATH_PATTERN",
    "PLUGIN_ID_PATTERN",
    "PROTOCOL_VERSION",
    "RELATIVE_PATH_PATTERN",
    "SEMVER_PATTERN",
    "SELF_REFERENCE_FIELDS",
    "ArtifactDigest",
    "BackendEntrypoint",
    "Capability",
    "CapabilityGrant",
    "DecisionCode",
    "EgressMode",
    "EgressPolicy",
    "FrontendEntrypoint",
    "GatewayDecision",
    "MigrationEngine",
    "MigrationsDeclaration",
    "PluginClaims",
    "PluginInstance",
    "PluginInstanceStatus",
    "PluginManifest",
    "PluginRpcOperations",
    "QuotaRef",
    "RpcOperationDeclaration",
    "RegistryOutcome",
    "RegistryResult",
]
