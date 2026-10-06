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
    assert ids == {
        "mod_prompts",
        "mod_mcp",
        "mod_files",
        "mod_equipment",
    }
    assert len(EXAMPLE_MODULE_IDS) == 4


def test_templates_meta_contract() -> None:
    """Templates live inside mod_equipment (hub tile + table + built-in seeds)."""
    meta = mod_equipment_meta()
    assert "templates" in {t["slug"] for t in meta["tables"]}
    cols = {c["name"]: c for c in meta["columns"] if c["table_slug"] == "templates"}
    assert set(cols) == {"template_type", "title", "file", "active"}
    assert set(cols["template_type"]["enum"]["values"]) == {
        "budget",
        "commercial_proposal",
        "specification",
    }
    assert cols["file"]["type"] == "file_ref"
    views = {v["slug"] for v in meta["views"]}
    assert "templates_list" in views and "template_form" in views
    hub = next(v for v in meta["views"] if v["slug"] == "equipment_hub")["ui_json"]
    tiles = [i.get("target", {}).get("view") for i in hub.get("items", [])]
    assert "templates_list" not in tiles  # шаблоны уехали в хаб «Управление»
    mgmt_hub = next(v for v in meta["views"] if v["slug"] == "equipment_hub_management")["ui_json"]
    mgmt_tiles = [i.get("target", {}).get("view") for i in mgmt_hub.get("items", [])]
    assert "templates_list" in mgmt_tiles
    seeds = [s for s in meta["seed_rows"]["items"] if s.get("table_slug") == "templates"]
    assert {s["body"]["template_type"] for s in seeds} == {
        "budget",
        "commercial_proposal",
        "specification",
    }
    assert all(s["body"]["file"]["storage_key"].startswith("builtin/") for s in seeds)



def test_prompts_meta_has_materialize_and_seed() -> None:
    meta = mod_prompts_meta()
    table_slugs = {t["slug"] for t in meta["tables"]}
    assert table_slugs == {"prompt_profiles", "profile_settings", "prompt_paths"}
    assert "agents_md" not in table_slugs
    assert "prompt_items" not in table_slugs

    assert meta["materialize"]
    rule = meta["materialize"][0]
    assert rule["id"] == "prompt_paths_files"
    assert rule["source"]["table_slug"] == "prompt_paths"
    assert rule["target"]["format"] == "prompt_paths"

    assert meta["seed_rows"]["items"]
    seed_tables = {i["table_slug"] for i in meta["seed_rows"]["items"]}
    assert seed_tables == {"prompt_profiles", "profile_settings", "prompt_paths"}
    path_seeds = [i for i in meta["seed_rows"]["items"] if i["table_slug"] == "prompt_paths"]
    assert len(path_seeds) == 7
    assert path_seeds[0]["row_id"] == "path_agents"
    assert path_seeds[0]["body"]["name"] == "AGENTS.md"
    assert path_seeds[0]["body"]["files_json"] == []
    assert any(p["body"]["path"] == "rules" for p in path_seeds)

    tab = meta["tabs"][0]
    assert tab["view_slug"] == "prompt_profiles_list"
    assert tab["subtitle"] == "Инструкции для агента"
    assert tab["icon"] == "psychology_outlined"
    assert tab["default_project_bind"] == "global"
    assert any(c["name"] == "project_ids" for c in meta["columns"])
    assert not any(c["table_slug"] == "prompt_paths" and c["name"] == "project_ids" for c in meta["columns"])
    path_col = next(c for c in meta["columns"] if c["table_slug"] == "prompt_paths" and c["name"] == "path")
    assert path_col["ui"]["normalize"] == "workspace_path"

    profiles_list = next(v for v in meta["views"] if v["slug"] == "prompt_profiles_list")
    assert profiles_list["ui_json"]["scaffold"]["title"]["ru"] == "Профили"
    assert profiles_list["ui_json"]["row_tap"] == {"kind": "open_view", "view": "prompts_hub"}
    cols = [c["field"] for c in profiles_list["ui_json"]["columns"]]
    assert cols == ["name", "project_ids", "prompts_count"]
    assert profiles_list["ui_json"]["columns"][2]["source"] == "aggregate.prompt_paths.files_json"

    form = next(v for v in meta["views"] if v["slug"] == "prompt_profiles_form")
    form_cols = [f["column"] for f in form["ui_json"]["fields"]]
    assert form_cols == ["project_ids", "name"]

    hub = next(v for v in meta["views"] if v["slug"] == "prompts_hub")
    assert hub["kind"] == "collection"
    assert hub["table_slug"] == "prompt_paths"
    assert hub["ui_json"]["context_bind"] == {"profile_id": "contextRowId"}
    assert "settings_view" not in hub["ui_json"]
    assert hub["ui_json"]["context_header"]["table_slug"] == "prompt_profiles"
    assert hub["ui_json"]["scaffold"]["title_template"]["ru"] == "Профиль {name}"
    assert hub["ui_json"]["inline_add"]["title"]["ru"] == "Добавить промпт"
    hub_cols = [c["field"] for c in hub["ui_json"]["columns"]]
    assert hub_cols == ["name", "path", "files_json"]
    assert hub["ui_json"]["columns"][2]["format"] == "list_count"
    assert hub["ui_json"]["profile_table"] == "prompt_profiles"
    assert hub["ui_json"]["row_tap"]["view"] == "prompt_path_settings"

    path_settings = next(v for v in meta["views"] if v["slug"] == "prompt_path_settings")
    assert path_settings["ui_json"]["title_template"]["ru"] == "Промпт {name}"
    path_fields = path_settings["ui_json"]["fields"]
    assert [f["column"] for f in path_fields] == ["name", "path", "files_json"]
    files_field = path_fields[2]
    assert files_field["widget"] == "prompt_files_editor"
    assert "section_title" not in files_field

    view_kinds = {v["slug"]: v["kind"] for v in meta["views"]}
    assert "profile_hub" not in view_kinds.values()
    assert "agents_form" not in view_kinds
    assert "rules_list" not in view_kinds


def test_management_tabs_have_unique_icons_and_subtitles() -> None:
    prompts = mod_prompts_meta()["tabs"][0]
    mcp = mod_mcp_meta()["tabs"][0]
    files = mod_files_meta()["tabs"][0]
    icons = {prompts["icon"], mcp["icon"], files["icon"]}
    assert len(icons) == 3
    assert mcp["subtitle"] == "Инструменты и интеграции"
    assert files["subtitle"] == "Дополнительные файлы для агента"
    assert prompts["default_project_bind"] == "global"
    assert mcp["default_project_bind"] == "global"
    assert files["default_project_bind"] == "global"


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
    tab = next(t for t in meta["tabs"] if t["id"] == "tab_equipment")
    assert tab["view_slug"] == "equipment_hub"
    assert tab["nav"]["placement"] == "data"
    assert tab["default_project_bind"] == "global"
    assert {t["slug"] for t in meta["tables"]} == {
        "catalogs",
        "request_lines",
        "found_groups",
        "found_offers",
        "equipment_types",
        "equipment_items",
        "equipment_builds",
        "trusted_sellers",
        "web_shops",
        "templates",
        "equipment_mcp",
        "budget_lines",
        "procurement",
        "equipment_prompts",
        "mcp_tool_overrides",
        "document_company_fields",
        "document_fields",
    }
    kinds = {a["kind"] for a in meta["actions"]}
    assert "content.index_opensearch" in kinds
    assert "content.probe_remote_sql" in kinds
    assert "data.select_row" in kinds
    # WAVE7: пайплайн + закупка
    assert "equipment.pipeline" in kinds
    assert "equipment.procurement_apply" in kinds
    assert "equipment.budget_sync" in kinds
    # WAVE7: вьюхи групп/закупки; плоский список офферов заменён группами
    view_slugs = {v["slug"] for v in meta["views"]}
    assert {"found_groups_list", "groups_for_line", "offers_for_group",
            "procurement_list", "supplier_offers"} <= view_slugs
    assert "found_offers_list" not in view_slugs
    assert "offers_for_line" not in view_slugs
    # «Закупка» — проектный агрегат: общий датасет (chats=all), а не per-chat
    # зеркала; дрилл-даун поставщика читает офферы всех чатов проекта.
    tables = {t["slug"]: t for t in meta["tables"]}
    assert tables["procurement"]["scope"]["chats"] == "all"
    # Реквизиты компании — кабинетный контур (одинаковы во всех чатах),
    # deal-реквизиты — per-chat
    assert tables["document_company_fields"]["scope"] == {"projects": "all", "chats": "all"}
    assert tables["document_fields"]["scope"]["chats"] == "current"
    assert tables["found_offers"]["scope"]["chats"] == "current"
    supplier_offers = next(v for v in meta["views"] if v["slug"] == "supplier_offers")
    assert supplier_offers["ui_json"]["data_scope"] == {"chats": "all"}
    # дрилл-даун из «Закупки»: заголовок с именем поставщика из контекстной строки
    assert supplier_offers["ui_json"]["scaffold"]["title_template"]["ru"] == "Товары {seller}"
    # лицо группы: флаг «устарело» для warning-подсветки + колонка hidden
    fg_cols = {c["name"]: c for c in meta["columns"] if c["table_slug"] == "found_groups"}
    assert fg_cols["face_stale"]["hidden"] is True
    groups_list_view = next(v for v in meta["views"] if v["slug"] == "found_groups_list")
    assert groups_list_view["ui_json"]["row_style"][0] == {
        "when": {"field": "face_stale", "eq": True},
        "accent": "warning",
    }
    # MCP: у агента есть delete-инструменты; статусы позиций ему не принадлежат
    alias_tools = {a["tool"] for a in meta["mcp_aliases"]}
    assert {"request_lines_delete", "found_groups_delete"} <= alias_tools
    assert not any(r["target"]["format"] == "merge_mapped_sqlite" for r in meta["materialize"])
    assert any(r["id"] == "catalog_manifest" for r in meta["materialize"])
    # s4b is fully removed (table, views, hub tile, materialize, env)
    assert not any(r["id"] == "s4b_mcp_package" for r in meta["materialize"])
    assert any(r["id"] == "equipment_mcp_package" for r in meta["materialize"])
    assert not any(r["target"]["format"] == "copy_blob" for r in meta["materialize"])
    assert meta["container_env"] == []
    assert meta["container_env_secrets"] == []
    catalog_cols = {c["name"] for c in meta["columns"] if c["table_slug"] == "catalogs"}
    assert {"source_kind", "remote_dsn", "remote_table", "remote_database", "remote_dsn_has_database", "remote_user", "remote_password"} <= catalog_cols
    assert {"last_indexed_at", "reindex_interval_hours", "index_name"} <= catalog_cols
    # indexing progress heartbeat fields (hidden; «В процессе (x из y)»)
    assert {"indexed_count", "total_rows", "indexing_started_at"} <= catalog_cols
    progress_cols = {
        c["name"]: c
        for c in meta["columns"]
        if c["table_slug"] == "catalogs"
        and c["name"] in {"indexed_count", "total_rows", "indexing_started_at"}
    }
    assert all(c.get("read_only") is True for c in progress_cols.values())
    assert all(c.get("hidden") is True for c in progress_cols.values())
    reindex_col = next(
        c
        for c in meta["columns"]
        if c["table_slug"] == "catalogs" and c["name"] == "reindex_interval_hours"
    )
    assert reindex_col["label"]["ru"] == "Интервал обновления (ч)"
    assert reindex_col["type"] == "number"
    assert "artifact_ref" not in catalog_cols
    settings = next(v for v in meta["views"] if v["slug"] == "catalogs_settings")
    field_cols = [f["column"] for f in settings["ui_json"]["fields"]]
    assert field_cols[:3] == ["name", "status", "source_kind"]
    status_field = next(f for f in settings["ui_json"]["fields"] if f["column"] == "status")
    assert status_field.get("read_only") is True
    assert status_field.get("trailing_action", {}).get("action_id") == "index_catalog_opensearch"
    assert settings["ui_json"]["poll_while"]["equals"] == "indexing"
    assert status_field.get("accent_map") == {
        "draft": "warning",
        "indexing": "warning",
        "ready": "success",
        "error": "error",
    }
    status_col = next(c for c in meta["columns"] if c["table_slug"] == "catalogs" and c["name"] == "status")
    assert status_col["enum"]["labels"]["draft"] == "Без индексирования"
    assert status_col["enum"]["labels"]["indexing"] == "В процессе"
    assert status_col["enum"]["labels"]["ready"] == "Обработано"
    assert "remote_dsn" in field_cols
    assert "remote_table" in field_cols
    assert "remote_database" in field_cols
    assert "remote_user" in field_cols
    assert "remote_password" in field_cols
    table_field = next(f for f in settings["ui_json"]["fields"] if f["column"] == "remote_table")
    assert table_field["widget"] == "remote_table_picker"
    db_field = next(f for f in settings["ui_json"]["fields"] if f["column"] == "remote_database")
    assert db_field["widget"] == "remote_database_picker"
    catalogs_list = next(v for v in meta["views"] if v["slug"] == "catalogs_list")
    list_fields = [c["field"] for c in catalogs_list["ui_json"]["columns"]]
    assert "source_kind" in list_fields
    assert "status" in list_fields
    assert catalogs_list["ui_json"]["poll_while"] == {
        "field": "status",
        "equals": "indexing",
        "interval_ms": 3000,
    }
    status_cell = next(
        c for c in catalogs_list["ui_json"]["columns"] if c["field"] == "status"
    )
    assert status_cell.get("format") == "index_progress"
    assert catalogs_list["ui_json"]["row_style"][0]["accent"] == "error"
    accents = {r["when"]["eq"]: r["accent"] for r in catalogs_list["ui_json"]["row_style"] if r.get("when", {}).get("field") == "status"}
    assert accents["draft"] == "warning"
    assert accents["indexing"] == "warning"
    assert accents["ready"] == "success"
    actions = {a["id"]: a for a in meta["actions"]}
    assert actions["list_catalog_remote_tables"]["kind"] == "content.list_remote_sql_tables"
    assert actions["list_catalog_remote_databases"]["kind"] == "content.list_remote_sql_databases"
    assert "default_remote_table" not in actions["probe_catalog_remote"]["params"]
    assert "remote_auth_failed" in catalog_cols
    user_field = next(f for f in settings["ui_json"]["fields"] if f["column"] == "remote_user")
    assert "any" in user_field["visible_when"]
    tool_names = {t["name"] for t in meta["mcp_tools"]}
    # legacy disabled tool + duplicate of the first-party MCP tool removed
    assert "equipment_catalog_query" not in tool_names
    assert "equipment_offers_upsert" not in tool_names
    assert "equipment_types_list" in tool_names
    assert "equipment_items_upsert" in tool_names
    assert "equipment_builds_list" in tool_names
    assert "equipment_builds_upsert" in tool_names
    # sellers / web shops are READ-ONLY for the agent
    assert "trusted_sellers_list" in tool_names
    assert "trusted_sellers_upsert" not in tool_names
    assert "web_shops_list" in tool_names
    assert "web_shops_upsert" not in tool_names
    # budget page: read-only MCP tool + sync/export actions
    assert "budget_lines_list" in tool_names
    action_map = {a["id"]: a for a in meta["actions"]}
    assert action_map["budget_sync_lines"]["kind"] == "equipment.budget_sync"
    assert action_map["budget_export"]["kind"] == "equipment.budget_export"
    assert action_map["budget_sync_lines"]["params"]["budget_table"] == "budget_lines"

    hub = next(v for v in meta["views"] if v["slug"] == "equipment_hub")
    hub_titles = {i["title"] for i in hub["ui_json"]["items"]}
    # Данные: рабочие таблицы подбора
    assert "Характеристики оборудования" in hub_titles
    assert "Сборка" in hub_titles
    assert "Бюджетирование" in hub_titles
    assert "Позиции заказчика" in hub_titles
    assert "Найденные товары" in hub_titles
    assert "Закупка" in hub_titles
    # Управление уехало в отдельный хаб
    assert "Типы комплектующих" not in hub_titles
    assert "Поставщики" not in hub_titles
    assert "Базы данных" not in hub_titles
    assert "Интернет магазины" not in hub_titles
    assert "Шаблоны" not in hub_titles
    assert "S4B" not in hub_titles
    mgmt_hub = next(v for v in meta["views"] if v["slug"] == "equipment_hub_management")
    mgmt_titles = {i["title"] for i in mgmt_hub["ui_json"]["items"]}
    assert mgmt_titles == {
        "Базы данных",
        "Типы комплектующих",
        "Поставщики",
        "Интернет магазины",
        "Шаблоны",
        "Промпты",
        "Инструкции MCP",
    }
    tabs = {t["id"]: t for t in meta["tabs"]}
    assert tabs["tab_equipment"]["nav"]["placement"] == "data"
    assert tabs["tab_equipment_management"]["nav"]["placement"] == "management"
    assert tabs["tab_equipment_management"]["view_slug"] == "equipment_hub_management"
    budget_view = next(v for v in meta["views"] if v["slug"] == "budget_lines_list")
    assert budget_view["table_slug"] == "budget_lines"
    assert budget_view["ui_json"]["row_tap"] == {
        "kind": "open_view",
        "view": "groups_for_line",
        "context_field": "line_id",
    }
    assert budget_view["ui_json"]["chat_header"]["icon"] == "request_quote"
    budget_cols = [c["field"] for c in budget_view["ui_json"]["columns"]]
    assert "price_in" in budget_cols
    assert any(c.get("format") == "budget_calc" and c.get("variant") == "price_out" for c in budget_view["ui_json"]["columns"])
    assert any(c.get("format") == "budget_calc" and c.get("variant") == "margin_total" for c in budget_view["ui_json"]["columns"])
    # Реквизиты документов: компания (кабинет) + сделка (чат); дефолты
    # компании = значения из шаблона (сразу подставляются в форму); номера
    # договора/спецификации автогенерируются в UI из seq-счётчиков.
    doc_fields = budget_view["ui_json"]["doc_fields"]
    assert doc_fields["company_table"] == "document_company_fields"
    assert doc_fields["deal_table"] == "document_fields"
    # две карточки в линию без общего заголовка: «Поставщик» + «Сделка»
    assert "title" not in doc_fields
    assert doc_fields["company_title"]["ru"] == "Поставщик"
    assert doc_fields["deal_title"]["ru"] == "Сделка"
    company_panel_cols = [f["column"] for f in doc_fields["company_fields"]]
    assert {"supplier_name", "city", "app_number", "kp_valid_days"} <= set(company_panel_cols)
    # сроки поставки/оплаты — сделочные (per-chat), не кабинетные
    assert not {"delivery_days", "payment_days", "lead_time_note"} & set(company_panel_cols)
    deal_panel_cols = [f["column"] for f in doc_fields["deal_fields"]]
    assert {"delivery_days", "payment_days", "lead_time_note"} <= set(deal_panel_cols)
    auto = {f["column"]: f["auto"] for f in doc_fields["deal_fields"] if "auto" in f}
    assert auto == {
        "contract_number": "contract_seq",
        "spec_number": "spec_seq",
        "contract_date": "today",
    }
    company_cols = {
        c["name"]: c for c in meta["columns"] if c["table_slug"] == "document_company_fields"
    }
    assert company_cols["supplier_name"]["default"] == 'ООО "ИТ Взлёт"'
    assert company_cols["city"]["default"] == "г. Москва"
    assert company_cols["app_number"]["default"] == "1"
    assert company_cols["kp_valid_days"]["default"] == 2
    assert company_cols["contract_seq"]["hidden"] is True
    assert company_cols["spec_seq"]["hidden"] is True
    assert not {"delivery_days", "payment_days", "lead_time_note"} & set(company_cols)
    deal_cols = {c["name"] for c in meta["columns"] if c["table_slug"] == "document_fields"}
    assert {"customer_name", "contract_number", "spec_number", "delivery_address"} <= deal_cols
    assert {"delivery_days", "payment_days", "lead_time_note"} <= deal_cols
    # экспорты КП/Спецификации/бюджета мерджат компанию и сделку
    for action_id in ("budget_export", "kp_export", "spec_export"):
        params = action_map[action_id]["params"]
        assert params["company_fields_table"] == "document_company_fields"
        assert params["fields_table"] == "document_fields"
    budget_columns = {c["name"] for c in meta["columns"] if c["table_slug"] == "budget_lines"}
    assert {"line_id", "title", "part_number", "seller", "qty", "price_in", "vat", "markup"} <= budget_columns
    assert not any("s4b" in v["slug"] for v in meta["views"])

    items_list = next(v for v in meta["views"] if v["slug"] == "equipment_items_list")
    assert items_list["ui_json"]["inline_add"]["field"] == "name"
    item_settings = next(v for v in meta["views"] if v["slug"] == "equipment_item_settings")
    item_cols = [f["column"] for f in item_settings["ui_json"]["fields"]]
    assert item_cols[:3] == ["name", "offer_id", "type_id"]
    offer_field = next(f for f in item_settings["ui_json"]["fields"] if f["column"] == "offer_id")
    assert offer_field["widget"] == "type_ref_picker"
    assert offer_field["pick_view"] == "found_offers_pick"
    assert offer_field["title_field"] == "offer_title"
    type_field = next(f for f in item_settings["ui_json"]["fields"] if f["column"] == "type_id")
    assert type_field["widget"] == "type_ref_picker"
    assert type_field["empty_style"] == "warning"
    attrs_field = next(f for f in item_settings["ui_json"]["fields"] if f["column"] == "attrs")
    assert attrs_field["widget"] == "schema_attrs"
    assert attrs_field["section_title"]["ru"] == "Характеристики"

    types_pick = next(v for v in meta["views"] if v["slug"] == "equipment_types_pick")
    assert types_pick["ui_json"]["selection"]["set_on_context"]["field"] == "type_id"
    assert types_pick["ui_json"]["selection"]["control"] == "switch"
    assert types_pick["ui_json"]["selection"]["placement"] == "trailing"
    assert types_pick["ui_json"]["selection"]["disable_row_tap"] is True
    assert "row_tap" not in types_pick["ui_json"]

    offers_pick = next(v for v in meta["views"] if v["slug"] == "found_offers_pick")
    assert offers_pick["ui_json"]["selection"]["control"] == "switch"
    assert offers_pick["ui_json"]["selection"]["disable_row_tap"] is True
    assert offers_pick["ui_json"]["selection"]["set_on_context"]["field"] == "offer_id"

    items_pick = next(v for v in meta["views"] if v["slug"] == "equipment_items_pick")
    assert items_pick["ui_json"]["selection"]["set_on_context"]["map_field"] == "slots"
    assert items_pick["ui_json"]["row_filter_from_context"]["type_id"] == "_pick_type_id"

    type_settings = next(v for v in meta["views"] if v["slug"] == "equipment_type_settings")
    type_cols = [f["column"] for f in type_settings["ui_json"]["fields"]]
    assert type_cols[:2] == ["name", "build_scope"]
    scope_field = next(f for f in type_settings["ui_json"]["fields"] if f["column"] == "build_scope")
    assert scope_field["widget"] == "choice"
    fields_ed = next(f for f in type_settings["ui_json"]["fields"] if f["column"] == "fields_json")
    assert fields_ed["widget"] == "fields_schema_editor"

    types_list = next(v for v in meta["views"] if v["slug"] == "equipment_types_list")
    assert all(c["field"] != "sort_order" for c in types_list["ui_json"]["columns"])

    builds_list = next(v for v in meta["views"] if v["slug"] == "equipment_builds_list")
    assert builds_list["ui_json"]["inline_add"]["field"] == "name"
    build_settings = next(v for v in meta["views"] if v["slug"] == "equipment_build_settings")
    build_cols = [f["column"] for f in build_settings["ui_json"]["fields"]]
    assert build_cols == [
        "name",
        "build_kind",
        "components_count",
        "price_total",
        "slots",
    ]
    slots_field = next(f for f in build_settings["ui_json"]["fields"] if f["column"] == "slots")
    assert slots_field["widget"] == "build_slots"
    assert slots_field["pick_view"] == "equipment_items_pick"
    assert "section_title" not in slots_field

    seed_items = meta["seed_rows"]["items"]
    # 14 base seeds + 3 built-in template rows
    assert len(seed_items) == 17
    by_row = {s["row_id"]: s for s in seed_items}
    assert "tpl_budget_builtin" in by_row
    assert by_row["etype_cpu"]["body"]["name"] if "etype_cpu" in by_row else True
    etypes = [s for s in seed_items if s["table_slug"] == "equipment_types"]
    assert etypes and etypes[0]["body"]["sort_order"] == 10
    assert etypes[0]["body"]["build_scope"] == "all"
    assert any(f["key"] == "cores" for f in etypes[0]["body"]["fields_json"])
    assert any(f["key"] == "memory_channels" for f in etypes[0]["body"]["fields_json"])
    assert any(s["row_id"] == "etype_case_fans" for s in seed_items)
    assert any(s["row_id"] == "etype_bmc" for s in seed_items)
    mcp_seed = next(s for s in seed_items if s["row_id"] == "equipment_mcp_default")
    assert mcp_seed["table_slug"] == "equipment_mcp"
    assert mcp_seed["body"]["enabled"] is True
    assert mcp_seed["body"]["name"] == "prodavan-equipment"
    assert seed_items[-1]["row_id"] == "equipment_mcp_default"

    sellers_list = next(v for v in meta["views"] if v["slug"] == "trusted_sellers_list")
    assert sellers_list["ui_json"]["inline_add"]["field"] == "name"
    sellers_settings = next(
        v for v in meta["views"] if v["slug"] == "trusted_sellers_settings"
    )
    seller_fields = [f["column"] for f in sellers_settings["ui_json"]["fields"]]
    assert seller_fields[:2] == ["name", "aliases"]
    assert {
        "is_enabled",
        "is_verified",
        "payment_deferral",
        "priority_purchase",
        "margin_pct",
        "email",
        "comment",
        "inn",
    } <= set(seller_fields)

    shops_list = next(v for v in meta["views"] if v["slug"] == "web_shops_list")
    assert shops_list["ui_json"]["inline_add"]["field"] == "name"
    shops_settings = next(v for v in meta["views"] if v["slug"] == "web_shops_settings")
    shop_cols = [f["column"] for f in shops_settings["ui_json"]["fields"]]
    assert shop_cols == ["name", "url", "cookies"]
    cookies_field = next(
        f for f in shops_settings["ui_json"]["fields"] if f["column"] == "cookies"
    )
    assert cookies_field["widget"] == "text_editor"
    assert "max_lines" not in cookies_field

    catalogs = next(v for v in meta["views"] if v["slug"] == "catalogs_list")
    assert catalogs["ui_json"]["inline_add"]["field"] == "name"
    assert catalogs["ui_json"]["scaffold"]["title"]["ru"] == "Базы данных"
    assert catalogs["ui_json"]["empty"]["icon"] == "storage"
    assert "primary_action" not in catalogs["ui_json"]
    assert all(c.get("source") != "row.created_at" for c in catalogs["ui_json"]["columns"])

    settings = next(v for v in meta["views"] if v["slug"] == "catalogs_settings")
    file_field = next(
        f for f in settings["ui_json"]["fields"] if f["column"] == "source_file"
    )
    assert file_field["subtitle_from"] == "row_count"
    assert file_field["empty_style"] == "warning"
    assert any(f["column"] == "status" for f in settings["ui_json"]["fields"])
    name_field = next(f for f in settings["ui_json"]["fields"] if f["column"] == "name")
    assert name_field["icon"] == "storage"
    error_field = next(f for f in settings["ui_json"]["fields"] if f["column"] == "error")
    assert error_field["visible_when"] == {"field": "status", "eq": "error"}
    assert error_field["accent"] == "error"
    assert error_field["copy_on_tap"] is True
    map_field = next(f for f in settings["ui_json"]["fields"] if f["column"] == "column_map")
    assert map_field["widget"] == "column_map"
    map_col = next(c for c in meta["columns"] if c["name"] == "column_map")
    assert map_col["label"]["ru"] == "Сопоставление колонок"
    assert map_field["visible_when"] == {
        "field": "columns_json",
        "not_empty": True,
    }
    assert not any(f["column"] == "row_count" for f in settings["ui_json"]["fields"])
    paused_field = next(f for f in settings["ui_json"]["fields"] if f["column"] == "paused")
    assert paused_field["widget"] == "pause_toggle"
    assert paused_field["pause_label"]["ru"] == "Приостановить"
    assert paused_field["resume_label"]["ru"] == "Возобновить"
    assert paused_field["visible_when"] == {"field": "status", "eq": "ready"}
    meta_fields = [
        f
        for f in settings["ui_json"]["fields"]
        if f["column"] in ("column_map", "project_ids")
    ]
    map_vis = next(f for f in meta_fields if f["column"] == "column_map")["visible_when"]
    assert map_vis == {"field": "columns_json", "not_empty": True}
    proj_vis = next(f for f in meta_fields if f["column"] == "project_ids")["visible_when"]
    assert "any" in proj_vis

    catalogs_list = next(v for v in meta["views"] if v["slug"] == "catalogs_list")
    assert any(c["field"] == "status" for c in catalogs_list["ui_json"]["columns"])
    assert all(c["field"] != "added_at" for c in catalogs_list["ui_json"]["columns"])
    list_header = catalogs_list["ui_json"].get("list_header") or {}
    assert list_header.get("table_slug") == "equipment_mcp"
    assert list_header.get("ensure_row", {}).get("name") == "prodavan-equipment"
    mcp_fields = list_header.get("fields") or []
    assert any(
        f.get("column") == "file_ref"
        and f.get("widget") == "file_upload"
        and f.get("empty_style") == "warning"
        and f.get("subtitle_from") is None
        for f in mcp_fields
    )
    assert "toolbar" not in catalogs_list["ui_json"]
    assert any(t.get("slug") == "equipment_mcp" for t in meta["tables"])
    assert any(v["slug"] == "equipment_mcp_list" for v in meta["views"])
    assert any(r["id"] == "equipment_mcp_package" for r in meta["materialize"])
    map_schema = next(
        f for f in settings["ui_json"]["fields"] if f["column"] == "column_map"
    )["schema"]
    assert any(item["key"] == "brand" and item["label"]["ru"] == "Бренд" for item in map_schema)

    name_col = next(c for c in meta["columns"] if c["name"] == "name" and c["table_slug"] == "catalogs")
    assert name_col["label"]["ru"] == "Название"
    assert any(c["name"] == "paused" for c in meta["columns"])
    assert any(c["name"] == "column_map" for c in meta["columns"])
    assert any(
        c["name"] == "offer_id" and c["table_slug"] == "equipment_items" for c in meta["columns"]
    )
    assert any(
        c["name"] == "build_scope" and c["table_slug"] == "equipment_types" for c in meta["columns"]
    )

    assert not any(r["id"] == "catalog_merged_sqlite" for r in meta["materialize"])
    assert any(c["name"] == "index_name" for c in meta["columns"] if c["table_slug"] == "catalogs")
    assert actions["index_catalog_opensearch"]["kind"] == "content.index_opensearch"

    lines = next(v for v in meta["views"] if v["slug"] == "request_lines_list")
    assert lines["ui_json"]["scaffold"]["title"]["ru"] == "Позиции заказчика"
    assert lines["ui_json"].get("list_header") == list_header

    # WAVE7: «Найденные товары» = группы; on_load синк, тап → офферы группы
    groups_list = next(v for v in meta["views"] if v["slug"] == "found_groups_list")
    assert groups_list["ui_json"].get("list_header") == list_header
    assert groups_list["ui_json"]["on_load"] == {"action": "equipment_pipeline_sync"}
    assert groups_list["ui_json"]["row_tap"] == {
        "kind": "open_view",
        "view": "offers_for_group",
    }
    assert groups_list["ui_json"]["sort"] == [
        {"field": "rank", "dir": "asc"},
        {"field": "face_priority", "dir": "desc"},
        {"field": "face_price", "dir": "asc"},
        {"field": "face_in_stock", "dir": "desc"},
    ]
    # «Альтернативы» + «Выгода» в списке групп; цвет строк — только stale-warning
    group_fields = [c["field"] for c in groups_list["ui_json"]["columns"]]
    assert "alternatives_count" in group_fields
    assert "benefit" in group_fields
    assert all(
        r.get("when", {}).get("field") != "is_best"
        for r in groups_list["ui_json"]["row_style"]
    )
    assert any(
        c["name"] == "brand" and c["table_slug"] == "found_offers" for c in meta["columns"]
    )
    assert "remote_dsn_reachable" in catalog_cols
    db_picker = next(f for f in settings["ui_json"]["fields"] if f["column"] == "remote_database")
    reach = next(
        part for part in db_picker["visible_when"]["all"] if isinstance(part.get("any"), list)
    )
    assert any(clause.get("field") == "remote_dsn_reachable" for clause in reach["any"])
    line_col = next(
        c
        for c in meta["columns"]
        if c["name"] == "line_id" and c["table_slug"] == "found_offers"
    )
    assert line_col["required"] is False
    assert line_col["label"]["ru"] == "Запрос"
    # WAVE7: у группы line_id обязателен (позиция, для которой выбираем)
    group_line_col = next(
        c
        for c in meta["columns"]
        if c["name"] == "line_id" and c["table_slug"] == "found_groups"
    )
    assert group_line_col["required"] is True
    form = next(v for v in meta["views"] if v["slug"] == "found_offers_form")
    form_cols = [f["column"] for f in form["ui_json"]["fields"]]
    assert "catalog_id" not in form_cols
    assert "source_title" not in form_cols
    # связующие/вычисляемые поля в форме не редактируются: связь с позицией и
    # группой, точность и флаги выбора/staleness принадлежат пайплайну
    assert "line_id" not in form_cols
    assert "match_kind" not in form_cols
    assert "is_selected" not in form_cols
    assert "is_stale" not in form_cols
    pick = next(v for v in meta["views"] if v["slug"] == "request_lines_pick")
    also = pick["ui_json"]["selection"]["set_on_context"]["also_copy"]
    assert {"from": "title", "to": "source_title"} in also
