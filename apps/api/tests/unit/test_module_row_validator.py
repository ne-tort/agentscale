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


_SECRET_COLUMNS = [
    {"table_slug": "suppliers", "name": "name", "type": "text", "required": True},
    {"table_slug": "suppliers", "name": "api_token", "type": "secret_ref"},
]


def test_secret_ref_shape() -> None:
    body = validate_row_body(
        {
            "name": "ACME",
            "api_token": {
                "secret_ref": "file://cabinet_secrets/cab_1/sec_abc",
                "label": "S4B",
                "created_at": "2026-08-30T00:00:00+00:00",
            },
        },
        _SECRET_COLUMNS,
    )
    assert body["api_token"]["secret_ref"].startswith("file://cabinet_secrets/")


def test_secret_ref_bad_prefix() -> None:
    with pytest.raises(AppError) as exc:
        validate_row_body(
            {
                "name": "ACME",
                "api_token": {"secret_ref": "vault://ai_keys/x"},
            },
            _SECRET_COLUMNS,
        )
    assert "cabinet_secrets prefix" in (exc.value.detail or "")


def test_secret_ref_cabinet_scope() -> None:
    validate_row_body(
        {
            "name": "ACME",
            "api_token": {"secret_ref": "file://cabinet_secrets/cab_1/sec_abc"},
        },
        _SECRET_COLUMNS,
        cabinet_id="cab_1",
    )
    with pytest.raises(AppError) as exc:
        validate_row_body(
            {
                "name": "ACME",
                "api_token": {"secret_ref": "file://cabinet_secrets/cab_1/sec_abc"},
            },
            _SECRET_COLUMNS,
            cabinet_id="cab_other",
        )
    assert "belongs to cabinet cab_1" in (exc.value.detail or "")


_PATH_COLUMNS = [
    {
        "table_slug": "prompt_paths",
        "name": "path",
        "type": "text",
        "required": True,
        "default": "",
        "ui": {"normalize": "workspace_path"},
    },
]


def test_workspace_path_normalize_on_save() -> None:
    body = validate_row_body({"path": "\\skills\\"}, _PATH_COLUMNS)
    assert body["path"] == "skills"
    body2 = validate_row_body({"path": "/prompts/rules/"}, _PATH_COLUMNS)
    assert body2["path"] == "prompts/rules"
    body3 = validate_row_body({"path": ""}, _PATH_COLUMNS)
    assert body3["path"] == ""


def test_workspace_path_rejects_dotdot() -> None:
    with pytest.raises(AppError) as exc:
        validate_row_body({"path": "../etc"}, _PATH_COLUMNS)
    assert exc.value.code == "ROW_VALIDATION"
