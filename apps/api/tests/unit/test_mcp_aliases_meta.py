"""Unit tests for the mcp_aliases aggregation shape (module meta → chat map).

The aggregation lives in ProjectRuntimeModuleService.mcp_aliases_map; its
pure part (items → flat lookup) is exercised here via the documented item
shape. Validation behavior is covered by direct validator calls.
"""

from __future__ import annotations

import pytest

from prodavan.application.modules.module_meta_validator import validate_document_body
from prodavan.domain.errors import AppError


def _valid_items() -> list[dict]:
    return [
        {
            "server": "prodavan-equipment",
            "tool": "equipment_catalog_search",
            "label": "Поиск товара",
            "description": "Поиск позиций по каталогам",
        },
        {"tool": "equipment_catalog_sources", "label": "Источники каталога"},
    ]


def test_valid_aliases_document_passes() -> None:
    validate_document_body("mcp_aliases", _valid_items())


def test_slug_whitelisted() -> None:
    # Unknown slugs must still be rejected.
    with pytest.raises(AppError):
        validate_document_body("mcp_alias", _valid_items())


@pytest.mark.parametrize(
    "item",
    [
        {"tool": "", "label": "x"},
        {"tool": "t"},
        {"label": "x"},
        {"tool": "t", "label": ""},
        {"tool": "t", "label": "x", "server": 42},
        {"tool": "t", "label": "x", "description": 7},
        "not-an-object",
    ],
)
def test_bad_items_rejected(item: object) -> None:
    with pytest.raises(AppError) as exc:
        validate_document_body("mcp_aliases", [item])
    assert exc.value.status == 422


def test_duplicate_server_tool_pair_rejected() -> None:
    items = [
        {"server": "s", "tool": "t", "label": "a"},
        {"server": "s", "tool": "t", "label": "b"},
    ]
    with pytest.raises(AppError):
        validate_document_body("mcp_aliases", items)


def test_same_tool_different_servers_allowed() -> None:
    items = [
        {"server": "s1", "tool": "t", "label": "a"},
        {"server": "s2", "tool": "t", "label": "b"},
    ]
    validate_document_body("mcp_aliases", items)


def test_equipment_seed_aliases_validate() -> None:
    from prodavan.application.platform.product_module_seeds import mod_equipment_meta

    aliases = mod_equipment_meta().get("mcp_aliases")
    assert isinstance(aliases, list)
    assert len(aliases) == 8
    validate_document_body("mcp_aliases", aliases)
    # Every item must carry a non-empty server-qualified tool + label.
    for item in aliases:
        assert item["server"] == "prodavan-equipment"
        assert item["tool"]
        assert item["label"]
