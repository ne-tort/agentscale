"""Shared module row helpers — slug check, body coerce, defaults, validation."""

from __future__ import annotations

import json
import re
from typing import Any

from prodavan.application.modules.module_row_validator import (
    columns_for_table,
    validate_row_body,
)
from prodavan.domain.errors import AppError

_TABLE_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def check_table_slug(table_slug: str) -> str:
    table_slug = table_slug.strip().lower()
    if not _TABLE_SLUG_RE.match(table_slug):
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="bad table_slug",
        )
    return table_slug


def ensure_row_body(body: Any) -> dict:
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError as exc:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="body must be valid JSON",
            ) from exc
    if not isinstance(body, dict):
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="body must be a JSON object",
        )
    try:
        json.dumps(body)
    except (TypeError, ValueError) as exc:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="body is not JSON-serializable",
        ) from exc
    return body


def merge_column_defaults(*, columns_body: Any, table_slug: str, body: dict) -> dict:
    """Fill missing keys from columns[].default (request wins)."""
    if not isinstance(columns_body, list):
        return body
    merged = dict(body)
    for col in columns_body:
        if not isinstance(col, dict):
            continue
        if col.get("table_slug") != table_slug:
            continue
        name = col.get("name")
        if not isinstance(name, str) or not name or name in merged:
            continue
        if "default" in col:
            merged[name] = col["default"]
    return merged


def validate_row_with_columns(
    *,
    columns_body: Any,
    table_slug: str,
    body: dict,
    cabinet_id: str,
) -> dict:
    columns = columns_for_table(columns_body, table_slug)
    if not columns:
        return body
    return validate_row_body(body, columns, cabinet_id=cabinet_id)
