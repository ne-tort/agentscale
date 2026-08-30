"""Unit tests for module row body validation."""

from __future__ import annotations

import pytest

from prodavan.application.modules.module_row_validator import validate_row_body
from prodavan.domain.errors import AppError

_COLUMNS = [
    {"table_slug": "suppliers", "name": "name", "type": "text", "required": True},
    {
        "table_slug": "suppliers",
        "name": "status",
        "type": "enum",
        "enum": {"values": ["active", "blocked"]},
        "default": "active",
    },
    {"table_slug": "suppliers", "name": "attachment", "type": "file_ref"},
]


def test_valid_row_with_defaults() -> None:
    body = validate_row_body({"name": "ACME"}, _COLUMNS)
    assert body["name"] == "ACME"
    assert body["status"] == "active"


def test_missing_required_field() -> None:
    with pytest.raises(AppError) as exc:
        validate_row_body({"status": "active"}, _COLUMNS)
    assert exc.value.code == "ROW_VALIDATION"


def test_invalid_enum() -> None:
    with pytest.raises(AppError) as exc:
        validate_row_body({"name": "ACME", "status": "unknown"}, _COLUMNS)
    assert "invalid enum" in (exc.value.detail or "")


def test_file_ref_shape() -> None:
    body = validate_row_body(
        {
            "name": "ACME",
            "attachment": {
                "asset_id": "ca_1",
                "version_id": "cbv_1",
                "storage_key": "blobs/x",
                "filename": "a.pdf",
            },
        },
        _COLUMNS,
    )
    assert body["attachment"]["storage_key"] == "blobs/x"


def test_file_ref_missing_fields() -> None:
    with pytest.raises(AppError) as exc:
        validate_row_body(
            {"name": "ACME", "attachment": {"asset_id": "ca_1"}},
            _COLUMNS,
        )
    assert "file_ref missing fields" in (exc.value.detail or "")
