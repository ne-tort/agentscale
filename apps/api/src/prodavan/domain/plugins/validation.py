"""Валидация манифеста плагина v1 (F0).

Fail-closed: любая отсутствующая интеграция / неизвестное поле / расхождение
со схемой — явная :class:`PluginValidationError` с типизированным ``code``,
а не молчаливый успех.

При наличии ``jsonschema`` проверка идёт по draft 2020-12 схеме из
``packages/schemas/plugin-manifest/v1/schema.json``; при отсутствии библиотеки
выполняется эквивалентная ручная валидация с теми же кодами ошибок.
Семантические проверки (semver, allowlist capability, запрет самореференции,
нормализация plugin_id) выполняются всегда, независимо от бэкенда схемы.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Mapping, NoReturn

from prodavan.domain.plugins.types import (
    CAPABILITY_ALLOWLIST,
    EGRESS_REF_PATTERN,
    EMBEDDED_EGRESS_FIELDS,
    PATH_PATTERN,
    PLUGIN_ID_PATTERN,
    PROTOCOL_VERSION,
    RELATIVE_PATH_PATTERN,
    SEMVER_PATTERN,
    SELF_REFERENCE_FIELDS,
    ArtifactDigest,
    BackendEntrypoint,
    Capability,
    FrontendEntrypoint,
    MigrationEngine,
    MigrationsDeclaration,
    PluginManifest,
)

SCHEMA_RELATIVE_PATH = Path("packages/schemas/plugin-manifest/v1/schema.json")
PROTOCOL_RELATIVE_PATH = Path("packages/schemas/plugin-manifest/v1/protocol.json")
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
MANIFEST_ALLOWED_FIELDS: frozenset[str] = frozenset(MANIFEST_REQUIRED_FIELDS)


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
    UNKNOWN_FIELD = "unknown_field"
    MISSING_FIELD = "missing_field"


class PluginValidationError(ValueError):
    """Явная типизированная ошибка валидации манифеста плагина."""

    def __init__(self, code: ManifestValidationErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code.value}: {message}")


def _repo_root() -> Path:
    """Корень репозитория по расположению этого модуля (без обращения к git)."""
    # .../apps/api/src/prodavan/domain/plugins/validation.py -> repo root
    return Path(__file__).resolve().parents[6]


@lru_cache(maxsize=1)
def load_schema(path: Path | None = None) -> dict[str, Any]:
    """Загрузить JSON Schema манифеста как данные (без импортов из apps/web).

    Отсутствие файла — fail-closed ошибка, а не «пропустить проверку».
    """
    target = (path if path is not None else _repo_root() / SCHEMA_RELATIVE_PATH).resolve()
    if not target.is_file():
        raise PluginValidationError(
            ManifestValidationErrorCode.SCHEMA_UNAVAILABLE,
            f"schema file not found: {target}",
        )
    try:
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


def _check_path_array(value: Any, *, field_name: str, code: ManifestValidationErrorCode) -> tuple[str, ...]:
    """Массив уникальных абсолютных путей (public_pages / webhooks)."""
    if not isinstance(value, list):
        _fail(code, f"{field_name} must be an array")
    for item in value:
        _check_absolute_path(item, field_name=field_name, code=code)
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
        if not isinstance(item, str) or item not in CAPABILITY_ALLOWLIST:
            _fail(ManifestValidationErrorCode.CAPABILITY_UNKNOWN, f"unknown capability: {item!r}")
    if len(set(value)) != len(value):
        _fail(ManifestValidationErrorCode.CAPABILITY_DUPLICATE, "capabilities must not contain duplicates")
    return tuple(Capability(item) for item in value)


def _validate_with_jsonschema(data: Mapping[str, Any]) -> None:
    """Проверка по draft 2020-12 схеме, если библиотека доступна."""
    import jsonschema  # type: ignore[import-not-found]

    schema = load_schema()
    try:
        validator_cls = jsonschema.validators.validator_for(schema)
        validator_cls.check_schema(schema)
        validator = validator_cls(schema)
        errors = sorted(validator.iter_errors(dict(data)), key=lambda error: list(error.absolute_path))
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

    Нормализация ``plugin_id``: ``str.strip().lower()`` (после проверки pattern;
    значение уже должно быть в нижнем регистре, поэтому повторная проверка
    гарантирует отсутствие «тихого» изменения идентификатора).
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

    # 3. Проверка по JSON Schema (jsonschema при наличии), затем ручная проверка.
    # Ручная проверка обязательна в обоих случаях: она даёт стабильные коды ошибок.
    try:
        import jsonschema  # noqa: F401  # type: ignore[import-not-found]
    except ImportError:
        jsonschema_available = False
    else:
        jsonschema_available = True
    if jsonschema_available:
        _validate_with_jsonschema(payload)
    _validate_manual(payload)

    # 4. Нормализация plugin_id и сборка доменного типа (повторная валидация в __post_init__).
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
            ),
            webhooks=_check_path_array(
                payload["webhooks"],
                field_name="webhooks",
                code=ManifestValidationErrorCode.WEBHOOKS_INVALID,
            ),
            min_platform_version=payload["min_platform_version"],
        )
    except ValueError as exc:
        # __post_init__ доменных типов мог выбросить ValueError без кода —
        # превращаем в типизированную ошибку, чтобы не было «молчаливого» отказа.
        if isinstance(exc, PluginValidationError):
            raise
        _fail(ManifestValidationErrorCode.SCHEMA_INVALID, f"domain type rejected manifest: {exc}")


def _validate_manual(payload: Mapping[str, Any]) -> None:
    """Эквивалент schema.json без внешних зависимостей, с теми же кодами ошибок."""
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
    _check_path_array(
        payload["public_pages"],
        field_name="public_pages",
        code=ManifestValidationErrorCode.PUBLIC_PAGES_INVALID,
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
    "ManifestValidationErrorCode",
    "PluginValidationError",
    "load_schema",
    "validate_artifact_digest",
    "validate_manifest",
]
