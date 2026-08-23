"""Safe SQL identifier helpers for cabinet schemas (L06)."""

from __future__ import annotations

from prodavan.domain.errors import AppError


def qident(name: str) -> str:
    """Quote SQL identifier after strict alphanumeric+underscore validation."""
    if not name or not name.replace("_", "").isalnum():
        raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad identifier")
    return f'"{name}"'


def qualified(schema: str, table: str) -> str:
    return f"{qident(schema)}.{qident(table)}"


def data_table_slug(table_slug: str) -> str:
    if not table_slug.replace("_", "").isalnum() or not table_slug.islower():
        raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad table slug")
    return f"data_{table_slug}"
