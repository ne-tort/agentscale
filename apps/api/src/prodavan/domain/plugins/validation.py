"""Валидация манифеста плагина v1 (F0).

Fail-closed: любая отсутствующая интеграция / неизвестное поле / расхождение
со схемой — явная :class:`PluginValidationError` с типизированным ``code``,
а не молчаливый успех.

При наличии ``jsonschema`` проверка идёт по draft 2020-12 схеме из
``packages/schemas/plugin-manifest/v1/schema.json``; при отсутствии библиотеки
выполняется эквивалентная ручная валидация с теми же кодами ошибок.
Схема разрешается через абсолютный ``PLUGIN_SCHEMA_ROOT``, структурный repo
layout либо container layout ``/app/src`` → ``/packages/schemas``, но не cwd.
Семантические проверки (semver, allowlist capability, запрет самореференции,
нормализация plugin_id) выполняются всегда, независимо от бэкенда схемы.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, NoReturn

from prodavan.domain.plugins.types import (
    CAPABILITY_ALLOWLIST,
    EGRESS_REF_PATTERN,
    EMBEDDED_EGRESS_FIELDS,
    MCP_MAX_SERVERS,
    MCP_MAX_TOOLS_PER_SERVER,
    PATH_PATTERN,
    PUBLIC_PAGE_PATTERN,
    POD_MAX_ALLOWLIST,
    PLUGIN_CAPABILITY_ID_PATTERN,
    PLUGIN_CAPABILITY_VERSION_PATTERN,
    PLUGIN_ID_PATTERN,
    PLUGIN_OPERATION_PATTERN,
    PLUGIN_RPC_METHODS,
    PROTOCOL_VERSION,
    RELATIVE_PATH_PATTERN,
    SECRET_DELIVERY_MAX_ALLOWLIST,
    SEMVER_PATTERN,
    SELF_REFERENCE_FIELDS,
    ArtifactDigest,
    BackendEntrypoint,
    Capability,
    FrontendEntrypoint,
    McpServerDeclaration,
    McpToolDeclaration,
    MigrationEngine,
    MigrationsDeclaration,
    PluginManifest,
    PluginRpcOperations,
    PodAllowlistEntry,
    PodsDeclaration,
    RpcOperationDeclaration,
    SecretDeliveryDeclaration,
)

SCHEMA_RELATIVE_PATH = Path("packages/schemas/plugin-manifest/v1/schema.json")
PROTOCOL_RELATIVE_PATH = Path("packages/schemas/plugin-manifest/v1/protocol.json")
SCHEMA_ROOT_ENV = "PLUGIN_SCHEMA_ROOT"
CONTAINER_SOURCE_ROOT = Path("/app/src")
CONTAINER_SCHEMA_PATH = Path("/packages/schemas/plugin-manifest/v1/schema.json")
_SCHEMAS_RELATIVE_PATH = Path("plugin-manifest/v1/schema.json")
_REPO_SOURCE_RELATIVE_PATH = Path("apps/api/src")
MANIFEST_REQUIRED_FIELDS: tuple[str, ...] = (
    "plugin_id",
    "version",
    "protocol",
    "frontend",
    "backend",
    "capabilities",
    "migrations",
    "egress_ref",
    "public_pages",
    "webhooks",
    "min_platform_version",
)
MANIFEST_ALLOWED_FIELDS: frozenset[str] = frozenset(MANIFEST_REQUIRED_FIELDS) | {
    "rpc_operations", "mcp_servers", "pods", "secret_delivery",
}


class ManifestValidationErrorCode(StrEnum):
    """Типизированные коды ошибок валидации манифеста."""

    SCHEMA_INVALID = "schema_invalid"
    NOT_AN_OBJECT = "not_an_object"
    SCHEMA_UNAVAILABLE = "schema_unavailable"
    PLUGIN_ID_EMPTY = "plugin_id_empty"
    PLUGIN_ID_INVALID = "plugin_id_invalid"
    VERSION_INVALID = "version_invalid"
    PROTOCOL_INVALID = "protocol_invalid"
    CAPABILITY_UNKNOWN = "capability_unknown"
    CAPABILITY_DUPLICATE = "capability_duplicate"
    SELF_REFERENCE_FORBIDDEN = "self_reference_forbidden"
    EMBEDDED_EGRESS_FORBIDDEN = "embedded_egress_forbidden"
    EGRESS_REF_INVALID = "egress_ref_invalid"
    MIN_PLATFORM_VERSION_INVALID = "min_platform_version_invalid"
    FRONTEND_INVALID = "frontend_invalid"
    BACKEND_INVALID = "backend_invalid"
    MIGRATIONS_INVALID = "migrations_invalid"
    PUBLIC_PAGES_INVALID = "public_pages_invalid"
    WEBHOOKS_INVALID = "webhooks_invalid"
    RPC_OPERATIONS_INVALID = "rpc_operations_invalid"
    MCP_SERVERS_INVALID = "mcp_servers_invalid"
    PODS_INVALID = "pods_invalid"
    SECRET_DELIVERY_INVALID = "secret_delivery_invalid"
    UNKNOWN_FIELD = "unknown_field"
    MISSING_FIELD = "missing_field"


class PluginValidationError(ValueError):
    """Явная типизированная ошибка валидации манифеста плагина."""

    def __init__(self, code: ManifestValidationErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code.value}: {message}")


def _first_existing_schema(candidates: tuple[Path, ...]) -> Path | None:
    """Вернуть первый существующий файл, не пробрасывая ошибки FS наружу."""
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if resolved.is_file():
                return resolved
        except (OSError, RuntimeError):
            # Недоступный путь не является разрешением схемы; итог всегда fail-closed.
            continue
    return None


def _resolve_schema_path() -> Path:
    """Найти схему только в явно разрешённых repo/container layout.

    ``PLUGIN_SCHEMA_ROOT`` принимает либо корень репозитория, либо каталог
    ``packages/schemas``. Явная настройка имеет приоритет и не скрывает ошибку
    fallback'ом. Текущий рабочий каталог намеренно не участвует в поиске.
    """
    configured = os.environ.get(SCHEMA_ROOT_ENV, "").strip()
    if configured:
        try:
            root = Path(configured).expanduser()
        except (OSError, RuntimeError) as exc:
            raise PluginValidationError(
                ManifestValidationErrorCode.SCHEMA_UNAVAILABLE,
                f"invalid {SCHEMA_ROOT_ENV}: {exc}",
            ) from exc
        if not root.is_absolute():
            raise PluginValidationError(
                ManifestValidationErrorCode.SCHEMA_UNAVAILABLE,
                f"{SCHEMA_ROOT_ENV} must be an absolute path, got {configured!r}",
            )
        candidates = (root / SCHEMA_RELATIVE_PATH, root / _SCHEMAS_RELATIVE_PATH)
        target = _first_existing_schema(candidates)
        if target is not None:
            return target
        raise PluginValidationError(
            ManifestValidationErrorCode.SCHEMA_UNAVAILABLE,
            f"schema file not found via {SCHEMA_ROOT_ENV}; tried: {candidates}",
        )

    try:
        module_path = Path(__file__).resolve()
    except (OSError, RuntimeError) as exc:
        raise PluginValidationError(
            ManifestValidationErrorCode.SCHEMA_UNAVAILABLE,
            f"cannot resolve validation module path: {exc}",
        ) from exc

    # Repo layout: идём по реальным предкам без фиксированного индекса. После
    # ближайшего apps/api/src не поднимаемся в потенциально чужой внешний repo.
    for parent in module_path.parents:
        try:
            is_repo_root = (parent / _REPO_SOURCE_RELATIVE_PATH).is_dir()
        except OSError:
            is_repo_root = False
        if not is_repo_root:
            continue
        target = _first_existing_schema((parent / SCHEMA_RELATIVE_PATH,))
        if target is not None:
            return target
        break

    # Container layout из apps/api/Dockerfile: PYTHONPATH=/app/src и схемы
    # смонтированы отдельно в /packages/schemas. Фиксированный allowlist вместо cwd.
    try:
        container_source = CONTAINER_SOURCE_ROOT.resolve()
    except (OSError, RuntimeError):
        container_source = CONTAINER_SOURCE_ROOT
    if module_path.is_relative_to(container_source):
        target = _first_existing_schema((CONTAINER_SCHEMA_PATH,))
        if target is not None:
            return target

    raise PluginValidationError(
        ManifestValidationErrorCode.SCHEMA_UNAVAILABLE,
        f"schema file not found in allowed repo/container layouts; set {SCHEMA_ROOT_ENV}",
    )


def load_schema(path: Path | None = None) -> dict[str, Any]:
    """Загрузить JSON Schema манифеста как данные (без импортов из apps/web).

    Отсутствие файла — fail-closed ошибка, а не «пропустить проверку».
    """
    if path is None:
        target = _resolve_schema_path()
    else:
        try:
            target = path.resolve()
        except (OSError, RuntimeError) as exc:
            raise PluginValidationError(
                ManifestValidationErrorCode.SCHEMA_UNAVAILABLE,
                f"schema path cannot be resolved: {exc}",
            ) from exc
    return _load_schema_file(target)


@lru_cache(maxsize=8)
def _load_schema_file(target: Path) -> dict[str, Any]:
    try:
        if not target.is_file():
            raise PluginValidationError(
                ManifestValidationErrorCode.SCHEMA_UNAVAILABLE,
                f"schema file not found: {target}",
            )
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PluginValidationError(
            ManifestValidationErrorCode.SCHEMA_UNAVAILABLE,
            f"schema file is unreadable: {exc}",
        ) from exc
    if not isinstance(payload, dict):
        raise PluginValidationError(
            ManifestValidationErrorCode.SCHEMA_INVALID,
            "schema root must be an object",
        )
    return payload


def _fail(code: ManifestValidationErrorCode, message: str) -> NoReturn:
    """Единая точка генерации типизированной ошибки."""
    raise PluginValidationError(code, message)


def _require_object(value: Any, *, field_name: str, code: ManifestValidationErrorCode) -> Mapping[str, Any]:
    """Проверка «объект с запретом лишних полей» для вложенных структур."""
    if not isinstance(value, Mapping):
        _fail(code, f"{field_name} must be an object")
    return value


def _check_closed_object(
    value: Any,
    *,
    field_name: str,
    required: frozenset[str],
    allowed: frozenset[str],
    code: ManifestValidationErrorCode,
) -> Mapping[str, Any]:
    """Fail-closed проверка: отсутствующее/неизвестное поле = ошибка."""
    obj = _require_object(value, field_name=field_name, code=code)
    missing = sorted(required - set(obj))
    if missing:
        _fail(code, f"{field_name} is missing fields: {', '.join(missing)}")
    unknown = sorted(set(obj) - allowed)
    if unknown:
        _fail(code, f"{field_name} has unknown fields: {', '.join(unknown)}")
    return obj


def _check_relative_path(value: Any, *, field_name: str, code: ManifestValidationErrorCode) -> None:
    """Относительный путь внутри артефакта без выхода наверх."""
    if not isinstance(value, str) or not RELATIVE_PATH_PATTERN.fullmatch(value) or ".." in value:
        _fail(code, f"{field_name} must be a relative path without '..'")


def _check_absolute_path(value: Any, *, field_name: str, code: ManifestValidationErrorCode) -> None:
    """Абсолютный URL-путь без выхода наверх."""
    if not isinstance(value, str) or not PATH_PATTERN.fullmatch(value) or ".." in value:
        _fail(code, f"{field_name} must be an absolute path without '..'")


def _check_path_array(
    value: Any,
    *,
    field_name: str,
    code: ManifestValidationErrorCode,
    pattern: re.Pattern[str] = PATH_PATTERN,
) -> tuple[str, ...]:
    """Массив уникальных абсолютных путей (public_pages / webhooks)."""
    if not isinstance(value, list):
        _fail(code, f"{field_name} must be an array")
    for item in value:
        # Валидация до set(): нехешируемые элементы не должны давать TypeError
        # вместо типизированной PluginValidationError.
        if not isinstance(item, str) or not pattern.fullmatch(item) or ".." in item:
            _fail(code, f"{field_name} must be an absolute path without '..'")
    if len(set(value)) != len(value):
        _fail(code, f"{field_name} must not contain duplicates")
    return tuple(value)


def _validate_frontend(value: Any) -> FrontendEntrypoint:
    obj = _check_closed_object(
        value,
        field_name="frontend",
        required=frozenset({"entry", "base_path"}),
        allowed=frozenset({"entry", "base_path"}),
        code=ManifestValidationErrorCode.FRONTEND_INVALID,
    )
    _check_relative_path(obj["entry"], field_name="frontend.entry", code=ManifestValidationErrorCode.FRONTEND_INVALID)
    _check_absolute_path(obj["base_path"], field_name="frontend.base_path", code=ManifestValidationErrorCode.FRONTEND_INVALID)
    return FrontendEntrypoint(entry=obj["entry"], base_path=obj["base_path"])


def _validate_backend(value: Any) -> BackendEntrypoint:
    obj = _check_closed_object(
        value,
        field_name="backend",
        required=frozenset({"port", "health"}),
        allowed=frozenset({"port", "health"}),
        code=ManifestValidationErrorCode.BACKEND_INVALID,
    )
    port = obj["port"]
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        _fail(ManifestValidationErrorCode.BACKEND_INVALID, "backend.port must be an integer in 1..65535")
    _check_absolute_path(obj["health"], field_name="backend.health", code=ManifestValidationErrorCode.BACKEND_INVALID)
    return BackendEntrypoint(port=port, health=obj["health"])


def _validate_migrations(value: Any) -> MigrationsDeclaration:
    obj = _check_closed_object(
        value,
        field_name="migrations",
        required=frozenset({"engine", "path"}),
        allowed=frozenset({"engine", "path"}),
        code=ManifestValidationErrorCode.MIGRATIONS_INVALID,
    )
    engine = obj["engine"]
    if not isinstance(engine, str) or engine not in {item.value for item in MigrationEngine}:
        _fail(
            ManifestValidationErrorCode.MIGRATIONS_INVALID,
            "migrations.engine must be one of liquibase/flyway/alembic",
        )
    _check_relative_path(obj["path"], field_name="migrations.path", code=ManifestValidationErrorCode.MIGRATIONS_INVALID)
    return MigrationsDeclaration(engine=MigrationEngine(engine), path=obj["path"])


def _validate_capabilities(value: Any) -> tuple[Capability, ...]:
    """Явный allowlist: неизвестная capability (в т.ч. db.direct) = ошибка."""
    if not isinstance(value, list):
        _fail(ManifestValidationErrorCode.CAPABILITY_UNKNOWN, "capabilities must be an array")
    for item in value:
        # Проверка типа до set(): нехешируемое значение должно дать типизированную
        # ошибку, а не TypeError.
        if not isinstance(item, str) or item not in CAPABILITY_ALLOWLIST:
            _fail(ManifestValidationErrorCode.CAPABILITY_UNKNOWN, f"unknown capability: {item!r}")
    if len(set(value)) != len(value):
        _fail(ManifestValidationErrorCode.CAPABILITY_DUPLICATE, "capabilities must not contain duplicates")
    return tuple(Capability(item) for item in value)


def _validate_rpc_operations(value: Any) -> PluginRpcOperations:
    """Закрытый manifest allowlist RPC-операций для broker."""
    if not isinstance(value, list) or len(value) > 64:
        _fail(
            ManifestValidationErrorCode.RPC_OPERATIONS_INVALID,
            "rpc_operations must be an array of at most 64 items",
        )
    operations: list[RpcOperationDeclaration] = []
    for raw in value:
        obj = _check_closed_object(
            raw,
            field_name="rpc_operations",
            required=frozenset({"capability", "operation", "http_method", "backend_path"}),
            allowed=frozenset({"capability", "operation", "http_method", "backend_path", "public"}),
            code=ManifestValidationErrorCode.RPC_OPERATIONS_INVALID,
        )
        capability = _check_closed_object(
            obj["capability"],
            field_name="rpc_operations.capability",
            required=frozenset({"id", "version"}),
            allowed=frozenset({"id", "version"}),
            code=ManifestValidationErrorCode.RPC_OPERATIONS_INVALID,
        )
        if not isinstance(capability["id"], str) or not PLUGIN_CAPABILITY_ID_PATTERN.fullmatch(
            capability["id"]
        ):
            _fail(
                ManifestValidationErrorCode.RPC_OPERATIONS_INVALID,
                "rpc operation capability.id must be lowercase 'module.operation'",
            )
        if not isinstance(capability["version"], str) or not PLUGIN_CAPABILITY_VERSION_PATTERN.fullmatch(
            capability["version"]
        ):
            _fail(
                ManifestValidationErrorCode.RPC_OPERATIONS_INVALID,
                "rpc operation capability.version must be '<major>.<minor>'",
            )
        if not isinstance(obj["operation"], str) or not PLUGIN_OPERATION_PATTERN.fullmatch(obj["operation"]):
            _fail(ManifestValidationErrorCode.RPC_OPERATIONS_INVALID, "invalid RPC operation name")
        if not isinstance(obj["http_method"], str) or obj["http_method"] not in PLUGIN_RPC_METHODS:
            _fail(ManifestValidationErrorCode.RPC_OPERATIONS_INVALID, "http_method must be GET or POST")
        _check_absolute_path(
            obj["backend_path"],
            field_name="rpc_operations.backend_path",
            code=ManifestValidationErrorCode.RPC_OPERATIONS_INVALID,
        )
        if not obj["backend_path"].startswith("/api/"):
            _fail(
                ManifestValidationErrorCode.RPC_OPERATIONS_INVALID,
                "backend_path must stay under /api/ and cannot be an absolute URL",
            )
        public = obj.get("public", False)
        if not isinstance(public, bool):
            _fail(ManifestValidationErrorCode.RPC_OPERATIONS_INVALID, "public must be a boolean")
        operations.append(
            RpcOperationDeclaration(
                capability_id=capability["id"],
                capability_version=capability["version"],
                operation=obj["operation"],
                http_method=obj["http_method"],
                backend_path=obj["backend_path"],
                public=public,
            )
        )
    return PluginRpcOperations(tuple(operations))


def _validate_pods(value: Any) -> PodsDeclaration:
    """Validate the executor's exact instance/project/job scope tuples."""
    code = ManifestValidationErrorCode.PODS_INVALID
    obj = _check_closed_object(
        value,
        field_name="pods",
        required=frozenset({"allowlist"}),
        allowed=frozenset({"allowlist"}),
        code=code,
    )
    raw_entries = obj["allowlist"]
    if not isinstance(raw_entries, list) or not 1 <= len(raw_entries) <= POD_MAX_ALLOWLIST:
        _fail(code, "pods.allowlist must contain 1..256 entries")
    entries: list[PodAllowlistEntry] = []
    seen: set[tuple[str, str, str]] = set()
    try:
        for raw_entry in raw_entries:
            entry_obj = _check_closed_object(
                raw_entry,
                field_name="pods.allowlist",
                required=frozenset({"plugin_instance_id", "project_id", "job_id"}),
                allowed=frozenset({"plugin_instance_id", "project_id", "job_id"}),
                code=code,
            )
            entry = PodAllowlistEntry(
                plugin_instance_id=entry_obj["plugin_instance_id"],
                project_id=entry_obj["project_id"],
                job_id=entry_obj["job_id"],
            )
            key = (entry.plugin_instance_id, entry.project_id, entry.job_id)
            if key in seen:
                _fail(code, "pods.allowlist must not contain duplicate entries")
            seen.add(key)
            entries.append(entry)
        return PodsDeclaration(allowlist=tuple(entries))
    except PluginValidationError:
        raise
    except (TypeError, ValueError) as exc:
        _fail(code, f"invalid pods declaration: {exc}")


def _validate_secret_delivery(value: Any) -> SecretDeliveryDeclaration:
    """Validate a reference-only delivery declaration; never accept secret data."""
    code = ManifestValidationErrorCode.SECRET_DELIVERY_INVALID
    obj = _check_closed_object(
        value,
        field_name="secret_delivery",
        required=frozenset({"mode", "allowlist"}),
        allowed=frozenset({"mode", "allowlist"}),
        code=code,
    )
    raw_refs = obj["allowlist"]
    if (
        not isinstance(raw_refs, list)
        or not 1 <= len(raw_refs) <= SECRET_DELIVERY_MAX_ALLOWLIST
    ):
        _fail(code, "secret_delivery.allowlist must contain 1..256 secret refs")
    try:
        return SecretDeliveryDeclaration(
            mode=obj["mode"],
            allowlist=tuple(raw_refs),
        )
    except (TypeError, ValueError) as exc:
        _fail(code, f"invalid secret_delivery declaration: {exc}")


def _validate_mcp_servers(value: Any) -> tuple[McpServerDeclaration, ...]:
    """Проверить data-only MCP declarations и property-based uniqueness."""
    code = ManifestValidationErrorCode.MCP_SERVERS_INVALID
    if not isinstance(value, list) or len(value) > MCP_MAX_SERVERS:
        _fail(code, "mcp_servers must be an array of at most 256 items")
    servers: list[McpServerDeclaration] = []
    server_ids: set[str] = set()
    try:
        for raw_server in value:
            server = _check_closed_object(
                raw_server,
                field_name="mcp_servers",
                required=frozenset({"server_id", "tools"}),
                allowed=frozenset({"server_id", "tools"}),
                code=code,
            )
            raw_tools = server["tools"]
            if not isinstance(raw_tools, list) or not 1 <= len(raw_tools) <= MCP_MAX_TOOLS_PER_SERVER:
                _fail(code, "mcp server tools must contain 1..64 declarations")
            tools: list[McpToolDeclaration] = []
            tool_names: set[str] = set()
            for raw_tool in raw_tools:
                tool = _check_closed_object(
                    raw_tool,
                    field_name="mcp_servers.tools",
                    required=frozenset({"name", "description", "version", "input_schema"}),
                    allowed=frozenset({"name", "description", "version", "input_schema"}),
                    code=code,
                )
                declaration = McpToolDeclaration(
                    name=tool["name"],
                    description=tool["description"],
                    version=tool["version"],
                    input_schema=tool["input_schema"],
                )
                if declaration.name in tool_names:
                    _fail(code, "mcp tool names must be unique per server")
                tool_names.add(declaration.name)
                tools.append(declaration)
            declaration_server = McpServerDeclaration(
                server_id=server["server_id"], tools=tuple(tools),
            )
            if declaration_server.server_id in server_ids:
                _fail(code, "mcp server_id values must be unique")
            server_ids.add(declaration_server.server_id)
            servers.append(declaration_server)
    except PluginValidationError:
        raise
    except (TypeError, ValueError) as exc:
        _fail(code, f"invalid mcp_servers declaration: {exc}")
    return tuple(servers)


def _validate_with_jsonschema(data: Mapping[str, Any]) -> None:
    """Проверка по draft 2020-12 схеме, если библиотека доступна."""
    import jsonschema  # type: ignore[import-not-found]

    schema = load_schema()
    try:
        validator_cls = jsonschema.validators.validator_for(schema)
        validator_cls.check_schema(schema)
        validator = validator_cls(schema)
        # Ключ сортировки — строки: absolute_path может содержать и индексы
        # массивов (int), и ключи объектов (str), сравнение которых падает.
        errors = sorted(
            validator.iter_errors(dict(data)),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
    except Exception as exc:  # схема/библиотека повели себя неожиданно — это отказ, не успех
        _fail(ManifestValidationErrorCode.SCHEMA_INVALID, f"schema validation failed: {exc}")
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "<root>"
        _fail(ManifestValidationErrorCode.SCHEMA_INVALID, f"{location}: {first.message}")


def validate_manifest(data: dict) -> PluginManifest:
    """Проверить и нормализовать манифест плагина.

    Порядок проверок детерминирован: структура → запрещённые поля → semver →
    capability allowlist → вложенные объекты → сборка доменного типа.
    Любая ошибка — :class:`PluginValidationError` с ``code``.

    Нормализация ``plugin_id``: ``str.strip().lower()`` применяется только к
    уже прошедшему pattern значению; любое расхождение (пробелы/регистр) —
    отказ, а не тихое изменение идентификатора.
    """
    if not isinstance(data, Mapping):
        _fail(ManifestValidationErrorCode.NOT_AN_OBJECT, "manifest must be an object")
    payload: dict[str, Any] = dict(data)

    # 1. Самореференция и встроенный egress запрещены ДО любой другой проверки.
    present_self_reference = sorted(SELF_REFERENCE_FIELDS & set(payload))
    if present_self_reference:
        _fail(
            ManifestValidationErrorCode.SELF_REFERENCE_FORBIDDEN,
            f"manifest must not contain {', '.join(present_self_reference)}",
        )
    present_embedded_egress = sorted(EMBEDDED_EGRESS_FIELDS & set(payload))
    if present_embedded_egress:
        _fail(
            ManifestValidationErrorCode.EMBEDDED_EGRESS_FORBIDDEN,
            f"manifest must use egress_ref instead of {', '.join(present_embedded_egress)}",
        )

    # 2. Закрытый набор полей: отсутствующее/неизвестное поле = ошибка.
    missing = sorted(set(MANIFEST_REQUIRED_FIELDS) - set(payload))
    if missing:
        _fail(ManifestValidationErrorCode.MISSING_FIELD, f"missing fields: {', '.join(missing)}")
    unknown = sorted(set(payload) - MANIFEST_ALLOWED_FIELDS)
    if unknown:
        _fail(ManifestValidationErrorCode.UNKNOWN_FIELD, f"unknown fields: {', '.join(unknown)}")

    # 3. Ручная проверка обязательна всегда: она даёт стабильные коды ошибок.
    # Затем, при наличии jsonschema, — дополнительный контроль по живой схеме.
    _validate_manual(payload)
    try:
        import jsonschema  # noqa: F401  # type: ignore[import-not-found]
    except ImportError:
        jsonschema_available = False
    else:
        jsonschema_available = True
    if jsonschema_available:
        _validate_with_jsonschema(payload)

    # 4. Нормализация plugin_id: не молча «исправляем», а отказываем при расхождении.
    raw_plugin_id = payload["plugin_id"]
    if not isinstance(raw_plugin_id, str) or not raw_plugin_id.strip():
        _fail(ManifestValidationErrorCode.PLUGIN_ID_EMPTY, "plugin_id must not be empty")
    if not PLUGIN_ID_PATTERN.fullmatch(raw_plugin_id):
        _fail(ManifestValidationErrorCode.PLUGIN_ID_INVALID, "plugin_id must match ^[a-z][a-z0-9_]*$")
    normalized_plugin_id = raw_plugin_id.strip().lower()
    if normalized_plugin_id != raw_plugin_id:
        _fail(ManifestValidationErrorCode.PLUGIN_ID_INVALID, "plugin_id must already be normalized")

    try:
        return PluginManifest(
            plugin_id=normalized_plugin_id,
            version=payload["version"],
            protocol=payload["protocol"],
            frontend=_validate_frontend(payload["frontend"]),
            backend=_validate_backend(payload["backend"]),
            capabilities=_validate_capabilities(payload["capabilities"]),
            migrations=_validate_migrations(payload["migrations"]),
            egress_ref=payload["egress_ref"],
            public_pages=_check_path_array(
                payload["public_pages"],
                field_name="public_pages",
                code=ManifestValidationErrorCode.PUBLIC_PAGES_INVALID,
                pattern=PUBLIC_PAGE_PATTERN,
            ),
            webhooks=_check_path_array(
                payload["webhooks"],
                field_name="webhooks",
                code=ManifestValidationErrorCode.WEBHOOKS_INVALID,
            ),
            min_platform_version=payload["min_platform_version"],
            rpc_operations=_validate_rpc_operations(payload.get("rpc_operations", [])),
            mcp_servers=_validate_mcp_servers(payload.get("mcp_servers", [])),
            pods=_validate_pods(payload["pods"]) if "pods" in payload else None,
            secret_delivery=(
                _validate_secret_delivery(payload["secret_delivery"])
                if "secret_delivery" in payload
                else None
            ),
        )
    except ValueError as exc:
        # __post_init__ доменных типов мог выбросить ValueError без кода —
        # превращаем в типизированную ошибку, чтобы не было «молчаливого» отказа.
        if isinstance(exc, PluginValidationError):
            raise
        _fail(ManifestValidationErrorCode.SCHEMA_INVALID, f"domain type rejected manifest: {exc}")


def _validate_manual(payload: Mapping[str, Any]) -> None:
    """Эквивалент schema.json без внешних зависимостей, с теми же кодами ошибок."""
    raw_plugin_id = payload["plugin_id"]
    if not isinstance(raw_plugin_id, str) or not raw_plugin_id.strip():
        _fail(ManifestValidationErrorCode.PLUGIN_ID_EMPTY, "plugin_id must not be empty")
    if not PLUGIN_ID_PATTERN.fullmatch(raw_plugin_id):
        _fail(ManifestValidationErrorCode.PLUGIN_ID_INVALID, "plugin_id must match ^[a-z][a-z0-9_]*$")
    if not isinstance(payload["version"], str) or not SEMVER_PATTERN.fullmatch(payload["version"]):
        _fail(ManifestValidationErrorCode.VERSION_INVALID, "version must be a semantic version")
    if payload["protocol"] != PROTOCOL_VERSION:
        _fail(ManifestValidationErrorCode.PROTOCOL_INVALID, f"protocol must be {PROTOCOL_VERSION}")
    if not isinstance(payload["egress_ref"], str) or not EGRESS_REF_PATTERN.fullmatch(payload["egress_ref"]):
        _fail(ManifestValidationErrorCode.EGRESS_REF_INVALID, "egress_ref must reference a grant (grant:...)")
    if not isinstance(payload["min_platform_version"], str) or not SEMVER_PATTERN.fullmatch(
        payload["min_platform_version"]
    ):
        _fail(
            ManifestValidationErrorCode.MIN_PLATFORM_VERSION_INVALID,
            "min_platform_version must be a semantic version",
        )
    _validate_frontend(payload["frontend"])
    _validate_backend(payload["backend"])
    _validate_capabilities(payload["capabilities"])
    _validate_migrations(payload["migrations"])
    _validate_rpc_operations(payload.get("rpc_operations", []))
    _validate_mcp_servers(payload.get("mcp_servers", []))
    if "pods" in payload:
        _validate_pods(payload["pods"])
    if "secret_delivery" in payload:
        _validate_secret_delivery(payload["secret_delivery"])
    _check_path_array(
        payload["public_pages"],
        field_name="public_pages",
        code=ManifestValidationErrorCode.PUBLIC_PAGES_INVALID,
        pattern=PUBLIC_PAGE_PATTERN,
    )
    _check_path_array(
        payload["webhooks"],
        field_name="webhooks",
        code=ManifestValidationErrorCode.WEBHOOKS_INVALID,
    )


def validate_artifact_digest(value: str) -> ArtifactDigest:
    """Удобная обёртка для проверки digest из OCI/cosign (не из манифеста)."""
    try:
        return ArtifactDigest(value)
    except ValueError as exc:
        _fail(ManifestValidationErrorCode.SCHEMA_INVALID, str(exc))


__all__ = [
    "MANIFEST_ALLOWED_FIELDS",
    "MANIFEST_REQUIRED_FIELDS",
    "PROTOCOL_RELATIVE_PATH",
    "SCHEMA_RELATIVE_PATH",
    "SCHEMA_ROOT_ENV",
    "ManifestValidationErrorCode",
    "PluginValidationError",
    "load_schema",
    "validate_artifact_digest",
    "validate_manifest",
]
