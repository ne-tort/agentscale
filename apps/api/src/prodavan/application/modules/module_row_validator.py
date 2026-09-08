"""Validate module_data_rows.body against column definitions."""

from __future__ import annotations

from typing import Any

from prodavan.domain.errors import AppError

_FILE_REF_REQUIRED = frozenset({"asset_id", "version_id", "storage_key", "filename"})
_SECRET_REF_REQUIRED = frozenset({"secret_ref"})
_SECRET_REF_PREFIXES = ("file://cabinet_secrets/", "vault://cabinet_secrets/")


def _row_error(detail: str) -> AppError:
    return AppError(
        code="ROW_VALIDATION",
        title="Row validation error",
        status=422,
        detail=detail,
    )


def columns_for_table(columns_doc: Any, table_slug: str) -> list[dict[str, Any]]:
    if not isinstance(columns_doc, list):
        return []
    return [
        dict(col)
        for col in columns_doc
        if isinstance(col, dict) and col.get("table_slug") == table_slug
    ]


def validate_row_body(
    body: dict[str, Any],
    columns: list[dict[str, Any]],
    *,
    cabinet_id: str | None = None,
) -> dict[str, Any]:
    """Return sanitized body containing only declared column keys."""
    if not isinstance(body, dict):
        raise _row_error("row body must be a JSON object")

    sanitized: dict[str, Any] = {}
    for col in columns:
        name = col.get("name")
        if not isinstance(name, str) or not name:
            continue

        if name in body:
            value = body[name]
            _validate_field(name, value, col, cabinet_id=cabinet_id)
            sanitized[name] = _normalize_field_value(name, value, col)
        elif col.get("required") is True:
            raise _row_error(f"missing required field: {name}")
        elif "default" in col:
            sanitized[name] = _normalize_field_value(name, col["default"], col)

    return sanitized


def _normalize_field_value(name: str, value: Any, col: dict[str, Any]) -> Any:
    ui = col.get("ui")
    if not isinstance(ui, dict):
        return value
    if ui.get("normalize") != "workspace_path":
        return value
    if value is None:
        return value
    if not isinstance(value, str):
        raise _row_error(f"{name}: expected string for workspace_path normalize")
    from prodavan.application.pod_service.workspace_paths import normalize_workspace_path
    from prodavan.domain.errors import AppError

    try:
        return normalize_workspace_path(value)
    except AppError as exc:
        raise _row_error(f"{name}: {exc.detail}") from exc


def _validate_field(name: str, value: Any, col: dict[str, Any], *, cabinet_id: str | None = None) -> None:
    col_type = col.get("type")
    if not isinstance(col_type, str):
        return

    if value is None:
        if col.get("required") is True:
            raise _row_error(f"missing required field: {name}")
        return

    match col_type:
        case "text":
            if not isinstance(value, str):
                raise _row_error(f"{name}: expected string")
        case "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise _row_error(f"{name}: expected number")
        case "bool":
            if not isinstance(value, bool):
                raise _row_error(f"{name}: expected boolean")
        case "datetime":
            if not isinstance(value, str):
                raise _row_error(f"{name}: expected ISO8601 string")
        case "json":
            if not isinstance(value, (dict, list)):
                raise _row_error(f"{name}: expected object or array")
        case "enum":
            if not isinstance(value, str):
                raise _row_error(f"{name}: expected enum string")
            enum = col.get("enum")
            values = enum.get("values") if isinstance(enum, dict) else None
            if isinstance(values, list) and value not in values:
                raise _row_error(f"{name}: invalid enum value {value!r}")
        case "ref":
            if not isinstance(value, str):
                raise _row_error(f"{name}: expected ref row_id string")
        case "file_ref":
            _validate_file_ref(name, value)
        case "secret_ref":
            _validate_secret_ref(name, value, cabinet_id=cabinet_id)
        case _:
            raise _row_error(f"{name}: unsupported column type {col_type!r}")


def _validate_file_ref(name: str, value: Any) -> None:
    if not isinstance(value, dict):
        raise _row_error(f"{name}: file_ref must be an object")
    missing = sorted(_FILE_REF_REQUIRED - set(value.keys()))
    if missing:
        raise _row_error(f"{name}: file_ref missing fields: {', '.join(missing)}")
    for key in _FILE_REF_REQUIRED:
        field = value.get(key)
        if not isinstance(field, str) or not field.strip():
            raise _row_error(f"{name}: file_ref.{key} must be a non-empty string")


def _validate_secret_ref(name: str, value: Any, *, cabinet_id: str | None = None) -> None:
    if not isinstance(value, dict):
        raise _row_error(f"{name}: secret_ref must be an object")
    ref = value.get("secret_ref")
    if not isinstance(ref, str) or not ref.strip():
        raise _row_error(f"{name}: secret_ref.secret_ref must be a non-empty string")
    if not ref.startswith(_SECRET_REF_PREFIXES):
        raise _row_error(f"{name}: secret_ref must use cabinet_secrets prefix")
    if cabinet_id is not None:
        from prodavan.infrastructure.secrets.cabinet_secret_store import (
            assert_cabinet_secret_scope,
        )

        try:
            assert_cabinet_secret_scope(ref, cabinet_id)
        except AppError as exc:
            raise _row_error(f"{name}: {exc.detail}") from exc
    label = value.get("label")
    if label is not None and not isinstance(label, str):
        raise _row_error(f"{name}: secret_ref.label must be a string")
    created_at = value.get("created_at")
    if created_at is not None and not isinstance(created_at, str):
        raise _row_error(f"{name}: secret_ref.created_at must be a string")
