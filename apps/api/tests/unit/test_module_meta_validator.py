"""Unit tests for server-side module meta validator."""

from __future__ import annotations

import copy

import pytest

from prodavan.application.modules.module_meta_validator import (
    validate_document_body,
    validate_manifest,
    validate_merged_slug_map,
)
from prodavan.domain.errors import AppError


def _suppliers_slug_map() -> dict:
    return {
        "tables": [
            {
                "slug": "suppliers",
                "label": "Suppliers",
                "storage_kind": "json_document",
            }
        ],
        "columns": [
            {
                "table_slug": "suppliers",
                "name": "name",
                "type": "text",
                "required": True,
            }
        ],
        "views": [
            {
                "slug": "suppliers_list",
                "table_slug": "suppliers",
                "ui_json": {"kind": "collection"},
            }
        ],
        "tabs": [
            {"view_slug": "suppliers_list", "title": "Suppliers"},
        ],
    }


def test_valid_suppliers_manifest() -> None:
    validate_merged_slug_map(_suppliers_slug_map())


def test_invalid_table_slug() -> None:
    data = _suppliers_slug_map()
    data["tables"] = [{"slug": "Bad-Slug", "storage_kind": "json_document"}]
    with pytest.raises(AppError) as exc:
        validate_merged_slug_map(data)
    assert exc.value.code == "META_VALIDATION"
    assert "invalid table slug" in (exc.value.detail or "")


def test_tab_unknown_view() -> None:
    data = _suppliers_slug_map()
    data["tabs"] = [{"view_slug": "missing_view"}]
    with pytest.raises(AppError) as exc:
        validate_merged_slug_map(data)
    assert "unknown view" in (exc.value.detail or "")


def test_seed_rows_unknown_table() -> None:
    data = _suppliers_slug_map()
    data["seed_rows"] = {
        "items": [{"table_slug": "nope", "row_id": "row-1", "body": {}}]
    }
    with pytest.raises(AppError) as exc:
        validate_merged_slug_map(data)
    assert "seed_rows references unknown table" in (exc.value.detail or "")


def test_invalid_nav_contour() -> None:
    data = _suppliers_slug_map()
    data["tabs"] = [
        {
            "view_slug": "suppliers_list",
            "nav": {"contour": "invalid_contour"},
        }
    ]
    with pytest.raises(AppError) as exc:
        validate_merged_slug_map(data)
    assert "nav.contour" in (exc.value.detail or "")


def test_document_body_array_required() -> None:
    with pytest.raises(AppError):
        validate_document_body("tables", {"not": "array"})


def test_document_body_seed_rows_shape() -> None:
    validate_document_body("seed_rows", {"items": []})
    with pytest.raises(AppError):
        validate_document_body("seed_rows", [])


def test_validate_manifest_direct() -> None:
    slug_map = _suppliers_slug_map()
    from prodavan.application.modules.module_meta_validator import manifest_from_slug_map

    manifest = manifest_from_slug_map(slug_map)
    validate_manifest(manifest)
    broken = copy.deepcopy(manifest)
    broken["columns"][0]["type"] = "unknown_type"
    with pytest.raises(AppError) as exc:
        validate_manifest(broken)
    assert "invalid column type" in (exc.value.detail or "")


def test_container_env_documents_validate() -> None:
    slug_map = _suppliers_slug_map()
    slug_map["container_env"] = [
        {"env_name": "LOG_LEVEL", "value": "info", "when": ["project.launch"]},
    ]
    slug_map["container_env_secrets"] = [
        {"env_name": "API_TOKEN", "secret_ref": "file://tok_1", "when": ["project.launch"]},
        {
            "foreach_rows": {
                "table_slug": "suppliers",
                "field": "token",
                "env_name_prefix": "SUPPLIER_DSN_",
                "match": {"status": "ready"},
                "registry_env_name": "SUPPLIER_REMOTE",
            },
            "when": ["project.launch"],
        },
    ]
    validate_merged_slug_map(slug_map)


def test_container_env_invalid_env_name() -> None:
    slug_map = _suppliers_slug_map()
    slug_map["container_env"] = [{"env_name": "bad-name", "value": "info"}]
    with pytest.raises(AppError) as exc:
        validate_merged_slug_map(slug_map)
    assert "invalid env_name" in (exc.value.detail or "")


def test_valid_materialize_copy_blob_rule() -> None:
    slug_map = _suppliers_slug_map()
    slug_map["columns"].append(
        {"table_slug": "suppliers", "name": "attachment", "type": "file_ref"}
    )
    slug_map["materialize"] = [
        {
            "id": "suppliers_file",
            "when": ["project.created", "project.sync"],
            "source": {"type": "rows", "table_slug": "suppliers"},
            "target": {
                "workspace_path": "{{name}}.bin",
                "format": "copy_blob",
                "field": "attachment",
            },
        }
    ]
    validate_merged_slug_map(slug_map)


def test_materialize_unknown_table() -> None:
    slug_map = _suppliers_slug_map()
    slug_map["materialize"] = [
        {
            "source": {"type": "rows", "table_slug": "nope"},
            "target": {"workspace_path": "out.txt", "format": "raw"},
        }
    ]
    with pytest.raises(AppError) as exc:
        validate_merged_slug_map(slug_map)
    assert "unknown table" in (exc.value.detail or "")


def test_materialize_copy_blob_requires_field() -> None:
    slug_map = _suppliers_slug_map()
    slug_map["materialize"] = [
        {
            "source": {"type": "rows", "table_slug": "suppliers"},
            "target": {"workspace_path": "out.bin", "format": "copy_blob"},
        }
    ]
    with pytest.raises(AppError) as exc:
        validate_merged_slug_map(slug_map)
    assert "target.field" in (exc.value.detail or "")


def test_materialize_rejects_deprecated_single_brace_workspace_path() -> None:
    # META-P1c: single-brace {var} is deprecated; {{var}} is the only dialect.
    slug_map = _suppliers_slug_map()
    slug_map["materialize"] = [
        {
            "source": {"type": "rows", "table_slug": "suppliers"},
            "target": {"workspace_path": "{name}.bin", "format": "raw", "field": "name"},
        }
    ]
    with pytest.raises(AppError) as exc:
        validate_merged_slug_map(slug_map)
    assert "deprecated" in (exc.value.detail or "").lower()


def test_materialize_rejects_deprecated_single_brace_filter() -> None:
    slug_map = _suppliers_slug_map()
    slug_map["materialize"] = [
        {
            "source": {
                "type": "rows",
                "table_slug": "suppliers",
                "filter": {"name": "{active_profile_id}"},
            },
            "target": {"workspace_path": "out.bin", "format": "raw"},
        }
    ]
    with pytest.raises(AppError) as exc:
        validate_merged_slug_map(slug_map)
    detail = exc.value.detail or ""
    assert "deprecated" in detail.lower()
    assert "source.filter.name" in detail


def test_product_module_seeds_pass_meta_validation() -> None:
    from prodavan.application.platform.product_module_seeds import (
        mod_equipment_meta,
        mod_files_meta,
        mod_mcp_meta,
        mod_prompts_meta,
    )

    for meta in (
        mod_prompts_meta(),
        mod_mcp_meta(),
        mod_files_meta(),
        mod_equipment_meta(),
    ):
        validate_merged_slug_map(meta)
