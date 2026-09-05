"""Unit tests for product module seed definitions."""

from prodavan.application.platform.product_module_seeds import (
    EXAMPLE_MODULE_IDS,
    PRODUCT_MODULES,
    mod_files_meta,
    mod_mcp_meta,
    mod_prompts_meta,
)
from prodavan.application.platform.product_module_upsert import upsert_product_modules


def test_upsert_product_modules_helper_is_importable() -> None:
    assert callable(upsert_product_modules)
    assert len(PRODUCT_MODULES) == 3


def test_product_modules_replace_examples() -> None:
    ids = {m[0] for m in PRODUCT_MODULES}
    assert ids == {"mod_prompts", "mod_mcp", "mod_files"}
    assert len(EXAMPLE_MODULE_IDS) == 4


def test_prompts_meta_has_materialize_and_seed() -> None:
    meta = mod_prompts_meta()
    assert any(t["slug"] == "prompt_profiles" for t in meta["tables"])
    assert meta["materialize"]
    assert meta["seed_rows"]["items"]
    tab = meta["tabs"][0]
    assert tab["view_slug"] == "prompt_profiles_list"
    assert tab["subtitle"] == "Инструкции для агента"
    assert tab["icon"] == "psychology_outlined"
    assert any(c["name"] == "project_ids" for c in meta["columns"])


def test_management_tabs_have_unique_icons_and_subtitles() -> None:
    prompts = mod_prompts_meta()["tabs"][0]
    mcp = mod_mcp_meta()["tabs"][0]
    files = mod_files_meta()["tabs"][0]
    icons = {prompts["icon"], mcp["icon"], files["icon"]}
    assert len(icons) == 3
    assert mcp["subtitle"] == "Инструменты и интеграции"
    assert files["subtitle"] == "Дополнительные файлы для агента"


def test_mcp_inline_add_is_laconic() -> None:
    meta = mod_mcp_meta()
    coll = next(v for v in meta["views"] if v["slug"] == "mcp_packages_list")
    assert coll["ui_json"]["inline_add"]["title"] == "Добавить MCP"
    assert coll["ui_json"]["empty"]["title"]["ru"] == "Нет MCP"


def test_files_meta_has_file_ref_column() -> None:
    meta = mod_files_meta()
    cols = meta["columns"]
    assert any(c["name"] == "file_ref" and c["type"] == "file_ref" for c in cols)


def test_mcp_meta_has_zip_materialize_rule() -> None:
    meta = mod_mcp_meta()
    rules = meta["materialize"]
    assert any(r["target"]["format"] == "mcp_package" for r in rules)


def test_collection_views_use_inline_add_without_primary_action() -> None:
    for meta_fn in (mod_mcp_meta, mod_files_meta, mod_prompts_meta):
        views = meta_fn()["views"]
        collections = [v for v in views if v.get("kind") == "collection"]
        assert collections, meta_fn.__name__
        for coll in collections:
            ui = coll["ui_json"]
            assert "primary_action" not in ui
            assert ui["inline_add"]["title"]
