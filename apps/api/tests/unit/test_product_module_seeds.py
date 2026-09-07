"""Unit tests for product module seed definitions."""

from prodavan.application.platform.product_module_seeds import (
    EXAMPLE_MODULE_IDS,
    PRODUCT_MODULES,
    mod_equipment_meta,
    mod_files_meta,
    mod_mcp_meta,
    mod_prompts_meta,
)
from prodavan.application.platform.product_module_upsert import upsert_product_modules


def test_upsert_product_modules_helper_is_importable() -> None:
    assert callable(upsert_product_modules)
    assert len(PRODUCT_MODULES) == 4


def test_product_modules_replace_examples() -> None:
    ids = {m[0] for m in PRODUCT_MODULES}
    assert ids == {"mod_prompts", "mod_mcp", "mod_files", "mod_equipment"}
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


def test_equipment_meta_hub_on_data_placement() -> None:
    meta = mod_equipment_meta()
    tab = meta["tabs"][0]
    assert tab["view_slug"] == "equipment_hub"
    assert tab["nav"]["placement"] == "data"
    assert {t["slug"] for t in meta["tables"]} == {
        "catalogs",
        "request_lines",
        "found_offers",
    }
    kinds = {a["kind"] for a in meta["actions"]}
    assert "content.index_tabular" in kinds
    assert "data.select_row" in kinds
    assert any(r["target"]["format"] == "merge_mapped_sqlite" for r in meta["materialize"])
    assert not any(r["target"]["format"] == "copy_blob" for r in meta["materialize"])
    tool_names = {t["name"] for t in meta["mcp_tools"]}
    assert "equipment_catalog_query" in tool_names
    assert "equipment_offers_upsert" in tool_names

    catalogs = next(v for v in meta["views"] if v["slug"] == "catalogs_list")
    assert catalogs["ui_json"]["inline_add"]["field"] == "name"
    assert catalogs["ui_json"]["scaffold"]["title"]["ru"] == "Базы данных"
    assert catalogs["ui_json"]["empty"]["icon"] == "storage"
    assert "primary_action" not in catalogs["ui_json"]
    assert any(c.get("source") == "row.created_at" for c in catalogs["ui_json"]["columns"])

    settings = next(v for v in meta["views"] if v["slug"] == "catalogs_settings")
    file_field = next(
        f for f in settings["ui_json"]["fields"] if f["column"] == "source_file"
    )
    assert file_field["subtitle_from"] == "row_count"
    assert file_field["empty_style"] == "warning"
    assert not any(f["column"] == "status" for f in settings["ui_json"]["fields"])
    name_field = next(f for f in settings["ui_json"]["fields"] if f["column"] == "name")
    assert name_field["icon"] == "storage"
    error_field = next(f for f in settings["ui_json"]["fields"] if f["column"] == "error")
    assert error_field["visible_when"] == {"field": "status", "eq": "error"}
    map_field = next(f for f in settings["ui_json"]["fields"] if f["column"] == "column_map")
    assert map_field["widget"] == "column_map"
    map_col = next(c for c in meta["columns"] if c["name"] == "column_map")
    assert map_col["label"]["ru"] == "Сопоставление колонок"
    assert map_field["visible_when"]["eq"] == "ready"
    assert not any(f["column"] == "row_count" for f in settings["ui_json"]["fields"])
    paused_field = next(f for f in settings["ui_json"]["fields"] if f["column"] == "paused")
    assert paused_field["widget"] == "pause_toggle"
    assert paused_field["pause_label"]["ru"] == "Приостановить"
    assert paused_field["resume_label"]["ru"] == "Возобновить"
    assert paused_field["visible_when"]["in"] == ["ready", "error"]
    meta_fields = [
        f
        for f in settings["ui_json"]["fields"]
        if f["column"] in ("column_map", "project_ids")
    ]
    assert all(f["visible_when"]["eq"] == "ready" for f in meta_fields)

    catalogs_list = next(v for v in meta["views"] if v["slug"] == "catalogs_list")
    assert all(c["field"] != "status" for c in catalogs_list["ui_json"]["columns"])

    name_col = next(c for c in meta["columns"] if c["name"] == "name" and c["table_slug"] == "catalogs")
    assert name_col["label"]["ru"] == "Название"
    assert any(c["name"] == "paused" for c in meta["columns"])
    assert any(c["name"] == "column_map" for c in meta["columns"])

    merge_rule = next(r for r in meta["materialize"] if r["id"] == "catalog_merged_sqlite")
    assert merge_rule["source"]["filter"] == {"status": "ready", "paused": False}
    assert merge_rule["target"]["workspace_path"] == "catalogs/catalog.sqlite"

    lines = next(v for v in meta["views"] if v["slug"] == "request_lines_list")
    assert lines["ui_json"]["scaffold"]["title"]["ru"] == "Позиции заказчика"

    offers = next(v for v in meta["views"] if v["slug"] == "found_offers_list")
    assert offers["ui_json"]["inline_add"]["field"] == "title"
    line_col = next(c for c in meta["columns"] if c["name"] == "line_id")
    assert line_col["required"] is False
