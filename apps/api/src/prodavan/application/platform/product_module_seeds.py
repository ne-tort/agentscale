"""Product module catalog meta — used by Alembic seed and tests."""

from __future__ import annotations

from typing import Any

_BLOCK_TYPES = [
    "rules",
    "skills",
    "output_schema",
    "guardrails",
    "examples",
    "others",
]

_BLOCK_LABELS: dict[str, dict[str, str]] = {
    "rules": {"ru": "Правила", "en": "Rules"},
    "skills": {"ru": "Умения", "en": "Skills"},
    "output_schema": {"ru": "Схема ответа", "en": "Output schema"},
    "guardrails": {"ru": "Безопасность", "en": "Guardrails"},
    "examples": {"ru": "Примеры", "en": "Examples"},
    "others": {"ru": "Другое", "en": "Others"},
}

_BLOCK_INLINE_TITLES = {
    "rules": "Добавить правило",
    "skills": "Добавить умение",
    "output_schema": "Добавить схему ответа",
    "guardrails": "Добавить ограничение",
    "examples": "Добавить пример",
    "others": "Добавить запись",
}

_BLOCK_EMPTY_TITLES: dict[str, dict[str, str]] = {
    "rules": {"ru": "Нет правил", "en": "No rules"},
    "skills": {"ru": "Нет умений", "en": "No skills"},
    "output_schema": {"ru": "Нет схем", "en": "No schemas"},
    "guardrails": {"ru": "Нет ограничений", "en": "No guardrails"},
    "examples": {"ru": "Нет примеров", "en": "No examples"},
    "others": {"ru": "Нет записей", "en": "No items"},
}


def _empty(title_ru: str, title_en: str) -> dict[str, Any]:
    return {"title": {"ru": title_ru, "en": title_en}}


def _project_ids_column(table_slug: str) -> dict[str, Any]:
    return {
        "table_slug": table_slug,
        "name": "project_ids",
        "label": {"ru": "Проекты", "en": "Projects"},
        "type": "json",
        "required": False,
        "default": [],
        "ui": {"widget": "project_multiselect", "empty_means": "all"},
    }


def _collection_view(
    *,
    slug: str,
    table_slug: str,
    form_slug: str | None = None,
    open_view_slug: str | None = None,
    title_field: str = "name",
    label: str | dict[str, str] = "Имя",
    inline_title: str = "Добавить запись",
    empty: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if open_view_slug is not None:
        row_tap: dict[str, Any] = {"kind": "open_view", "view": open_view_slug}
    else:
        row_tap = {"kind": "open_form", "view": form_slug or ""}
    ui: dict[str, Any] = {
        "version": 1,
        "kind": "collection",
        "title_field": title_field,
        "subtitle_fields": [],
        "columns": [{"field": title_field, "label": label}],
        "row_tap": row_tap,
        "inline_add": {"field": title_field, "title": inline_title},
    }
    if empty is not None:
        ui["empty"] = empty
    return {
        "slug": slug,
        "table_slug": table_slug,
        "kind": "collection",
        "ui_json": ui,
    }


def _prompt_item_views(block_type: str) -> tuple[dict[str, Any], dict[str, Any]]:
    list_slug = f"{block_type}_list"
    form_slug = f"{block_type}_form"
    label = _BLOCK_LABELS.get(block_type, {"ru": block_type, "en": block_type})
    inline_title = _BLOCK_INLINE_TITLES.get(block_type, f"Добавить {block_type}")
    empty = _BLOCK_EMPTY_TITLES.get(block_type, {"ru": "Нет записей", "en": "No items"})
    coll = _collection_view(
        slug=list_slug,
        table_slug="prompt_items",
        form_slug=form_slug,
        label=label,
        inline_title=inline_title,
        empty={"title": empty},
    )
    ui = coll["ui_json"]
    ui["row_filter"] = {"block_type": block_type}
    ui["context_bind"] = {"profile_id": "contextRowId"}
    return (
        coll,
        {
            "slug": form_slug,
            "table_slug": "prompt_items",
            "kind": "form",
            "ui_json": {
                "version": 1,
                "kind": "form",
                "mode": "edit",
                "title": label,
                "fields": [
                    {"column": "name", "widget": "value"},
                    {"column": "body_md", "widget": "markdown_editor"},
                    {"column": "project_ids", "widget": "project_multiselect"},
                ],
                "hidden_defaults": {"block_type": block_type},
            },
        },
    )


def _prompts_materialize_rules() -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = [
        {
            "id": "agents_active_profile",
            "enabled": True,
            "when": ["project.created", "project.resumed", "project.sync"],
            "priority": 10,
            "source": {
                "type": "row",
                "table_slug": "agents_md",
                "row_id": "{active_profile_id}",
                "field": "body_md",
            },
            "target": {"workspace_path": "AGENTS.md", "format": "raw"},
        },
    ]
    path_by_block = {
        "rules": "rules/{{name}}.md",
        "skills": "skills/{{name}}.md",
        "output_schema": "prompts/output-schema/{{name}}.md",
        "guardrails": "prompts/guardrails/{{name}}.md",
        "examples": "prompts/examples/{{name}}.md",
        "others": "prompts/others/{{name}}.md",
    }
    prio = 20
    for block_type, ws_path in path_by_block.items():
        rules.append(
            {
                "id": f"{block_type}_active_profile",
                "enabled": True,
                "when": ["project.created", "project.resumed", "project.sync"],
                "priority": prio,
                "source": {
                    "type": "rows",
                    "table_slug": "prompt_items",
                    "filter": {
                        "profile_id": "{active_profile_id}",
                        "block_type": block_type,
                    },
                    "field": "body_md",
                },
                "target": {"workspace_path": ws_path, "format": "raw"},
            }
        )
        prio += 1
    return rules


def mod_prompts_meta() -> dict[str, list[Any]]:
    views: list[dict[str, Any]] = [
        _collection_view(
            slug="prompt_profiles_list",
            table_slug="prompt_profiles",
            open_view_slug="prompts_hub",
            inline_title="Добавить профиль",
            empty=_empty("Нет профилей", "No profiles"),
        ),
        {
            "slug": "prompt_profiles_form",
            "table_slug": "prompt_profiles",
            "kind": "form",
            "ui_json": {
                "version": 1,
                "kind": "form",
                "mode": "edit",
                "title": {"ru": "Профиль", "en": "Profile"},
                "fields": [
                    {"column": "name", "widget": "value"},
                    {"column": "project_ids", "widget": "project_multiselect"},
                ],
            },
        },
        {
            "slug": "prompts_hub",
            "kind": "profile_hub",
            "ui_json": {
                "version": 1,
                "kind": "profile_hub",
                "profile_table": "prompt_profiles",
                "settings_table": "profile_settings",
                "profile_id_field": "profile_id",
                "active_field": "active",
                "blocks": [
                    {
                        "title": "AGENTS.md",
                        "icon": "description",
                        "target": {"kind": "view", "view": "agents_form"},
                    },
                    *[
                        {
                            "title": _BLOCK_LABELS[b],
                            "icon": "article_outlined",
                            "target": {"kind": "view", "view": f"{b}_list"},
                        }
                        for b in _BLOCK_TYPES
                    ],
                ],
            },
        },
        {
            "slug": "agents_form",
            "table_slug": "agents_md",
            "kind": "form",
            "ui_json": {
                "version": 1,
                "kind": "form",
                "mode": "edit",
                "title": "AGENTS.md",
                "fields": [{"column": "body_md", "widget": "markdown_editor"}],
            },
        },
    ]
    for block_type in _BLOCK_TYPES:
        list_v, form_v = _prompt_item_views(block_type)
        views.extend([list_v, form_v])

    return {
        "tables": [
            {
                "slug": "prompt_profiles",
                "label": "Профили",
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
            },
            {
                "slug": "profile_settings",
                "label": "Настройки профиля",
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
            },
            {
                "slug": "agents_md",
                "label": "AGENTS.md",
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
            },
            {
                "slug": "prompt_items",
                "label": "Prompt items",
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
            },
        ],
        "columns": [
            {
                "table_slug": "prompt_profiles",
                "name": "name",
                "label": "Имя",
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "prompt_profiles",
                "name": "is_default",
                "label": "По умолчанию",
                "type": "bool",
                "required": False,
                "default": False,
            },
            _project_ids_column("prompt_profiles"),
            {
                "table_slug": "profile_settings",
                "name": "profile_id",
                "label": "Profile",
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "profile_settings",
                "name": "active",
                "label": "Active",
                "type": "bool",
                "required": False,
                "default": False,
            },
            {
                "table_slug": "agents_md",
                "name": "profile_id",
                "label": "Profile",
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "agents_md",
                "name": "body_md",
                "label": "Body",
                "type": "text",
                "required": False,
                "default": "",
            },
            {
                "table_slug": "prompt_items",
                "name": "profile_id",
                "label": "Profile",
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "prompt_items",
                "name": "block_type",
                "label": "Block",
                "type": "enum",
                "required": True,
                "enum": {"values": _BLOCK_TYPES},
            },
            {
                "table_slug": "prompt_items",
                "name": "name",
                "label": "Имя",
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "prompt_items",
                "name": "body_md",
                "label": "Body",
                "type": "text",
                "required": False,
                "default": "",
            },
            _project_ids_column("prompt_items"),
        ],
        "views": views,
        "tabs": [
            {
                "id": "tab_prompts",
                "title": "Промпты",
                "subtitle": "Инструкции для агента",
                "order": 10,
                "icon": "psychology_outlined",
                "view_slug": "prompt_profiles_list",
                "table_slug": "prompt_profiles",
                "enabled": True,
                "nav": {"contour": "employee", "placement": "management"},
            }
        ],
        "materialize": _prompts_materialize_rules(),
        "seed_rows": {
            "items": [
                {
                    "table_slug": "prompt_profiles",
                    "row_id": "profile_default",
                    "body": {"name": "Default", "is_default": True, "project_ids": []},
                },
                {
                    "table_slug": "profile_settings",
                    "row_id": "settings_default",
                    "body": {"profile_id": "profile_default", "active": True},
                },
                {
                    "table_slug": "agents_md",
                    "row_id": "profile_default",
                    "body": {
                        "profile_id": "profile_default",
                        "body_md": "# Agent\n\nEdit AGENTS.md in the cabinet UI.\n",
                    },
                },
            ]
        },
    }


def mod_files_meta() -> dict[str, list[Any]]:
    return {
        "tables": [
            {
                "slug": "files",
                "label": "Файлы",
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
            }
        ],
        "columns": [
            {
                "table_slug": "files",
                "name": "name",
                "label": "Имя",
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "files",
                "name": "target_path",
                "label": "Путь в workspace",
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "files",
                "name": "file_ref",
                "label": "Файл",
                "type": "file_ref",
                "required": False,
            },
            _project_ids_column("files"),
        ],
        "views": [
            _collection_view(
                slug="files_list",
                table_slug="files",
                form_slug="files_form",
                label="Имя",
                inline_title="Добавить файл",
                empty=_empty("Нет файлов", "No files"),
            ),
            {
                "slug": "files_form",
                "table_slug": "files",
                "kind": "form",
                "ui_json": {
                    "version": 1,
                    "kind": "form",
                    "mode": "edit",
                    "title": "Файл",
                    "fields": [
                        {"column": "name", "widget": "value"},
                        {"column": "target_path", "widget": "value"},
                        {"column": "file_ref", "widget": "file_upload"},
                        {"column": "project_ids", "widget": "project_multiselect"},
                    ],
                },
            },
        ],
        "tabs": [
            {
                "id": "tab_files",
                "title": "Файлы",
                "subtitle": "Дополнительные файлы для агента",
                "order": 30,
                "icon": "attach_file",
                "view_slug": "files_list",
                "table_slug": "files",
                "enabled": True,
                "nav": {"contour": "employee", "placement": "management"},
            }
        ],
        "materialize": [
            {
                "id": "files_to_workspace",
                "enabled": True,
                "when": ["project.created", "project.resumed", "project.sync"],
                "priority": 50,
                "source": {"type": "rows", "table_slug": "files"},
                "target": {
                    "workspace_path": "{{target_path}}",
                    "format": "copy_blob",
                    "field": "file_ref",
                },
            }
        ],
        "seed_rows": {"items": []},
    }


def mod_mcp_meta() -> dict[str, list[Any]]:
    return {
        "tables": [
            {
                "slug": "mcp_packages",
                "label": "MCP packages",
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
            }
        ],
        "columns": [
            {
                "table_slug": "mcp_packages",
                "name": "name",
                "label": "Имя",
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "mcp_packages",
                "name": "version",
                "label": "Version",
                "type": "text",
                "required": True,
                "default": "1.0.0",
            },
            {
                "table_slug": "mcp_packages",
                "name": "enabled",
                "label": "Enabled",
                "type": "bool",
                "required": False,
                "default": True,
            },
            {
                "table_slug": "mcp_packages",
                "name": "file_ref",
                "label": "Zip package",
                "type": "file_ref",
                "required": True,
            },
            _project_ids_column("mcp_packages"),
        ],
        "views": [
            _collection_view(
                slug="mcp_packages_list",
                table_slug="mcp_packages",
                form_slug="mcp_packages_form",
                inline_title="Добавить MCP",
                empty=_empty("Нет MCP", "No MCP"),
            ),
            {
                "slug": "mcp_packages_form",
                "table_slug": "mcp_packages",
                "kind": "form",
                "ui_json": {
                    "version": 1,
                    "kind": "form",
                    "mode": "edit",
                    "title": "MCP",
                    "fields": [
                        {"column": "name", "widget": "value"},
                        {"column": "version", "widget": "value"},
                        {"column": "enabled", "widget": "switch"},
                        {"column": "file_ref", "widget": "file_upload", "accept": ".zip"},
                        {"column": "project_ids", "widget": "project_multiselect"},
                    ],
                },
            },
        ],
        "tabs": [
            {
                "id": "tab_mcp",
                "title": "MCP",
                "subtitle": "Инструменты и интеграции",
                "order": 20,
                "icon": "hub",
                "view_slug": "mcp_packages_list",
                "table_slug": "mcp_packages",
                "enabled": True,
                "nav": {"contour": "employee", "placement": "management"},
            }
        ],
        "materialize": [
            {
                "id": "mcp_packages_enabled",
                "enabled": True,
                "when": ["project.created", "project.resumed", "project.sync"],
                "priority": 60,
                "source": {
                    "type": "rows",
                    "table_slug": "mcp_packages",
                    "filter": {"enabled": True},
                },
                "target": {
                    "workspace_path": "packages/{{name}}",
                    "format": "mcp_package",
                    "field": "file_ref",
                },
            }
        ],
        "seed_rows": {"items": []},
    }


def mod_equipment_meta() -> dict[str, list[Any]]:
    """Подбор техники — hub on Данные; catalogs + request lines + found offers."""
    return {
        "tables": [
            {
                "slug": "catalogs",
                "label": {"ru": "Базы данных", "en": "Databases"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
            },
            {
                "slug": "request_lines",
                "label": {"ru": "Позиции заказчика", "en": "Request lines"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
            },
            {
                "slug": "found_offers",
                "label": {"ru": "Найденные товары", "en": "Found offers"},
                "storage_kind": "json_document",
                "enabled": True,
                "scope": {"projects": "all"},
            },
        ],
        "columns": [
            {
                "table_slug": "catalogs",
                "name": "name",
                "label": {"ru": "Имя", "en": "Name"},
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "catalogs",
                "name": "source_file",
                "label": {"ru": "Файл", "en": "File"},
                "type": "file_ref",
                "required": False,
            },
            {
                "table_slug": "catalogs",
                "name": "artifact_ref",
                "label": {"ru": "SQLite", "en": "SQLite"},
                "type": "file_ref",
                "required": False,
            },
            {
                "table_slug": "catalogs",
                "name": "status",
                "label": {"ru": "Статус", "en": "Status"},
                "type": "enum",
                "required": True,
                "default": "draft",
                "enum": {
                    "values": ["draft", "indexing", "ready", "error"],
                    "labels": {
                        "draft": "Черновик",
                        "indexing": "Индексация",
                        "ready": "Готово",
                        "error": "Ошибка",
                    },
                },
            },
            {
                "table_slug": "catalogs",
                "name": "row_count",
                "label": {"ru": "Строк", "en": "Rows"},
                "type": "number",
                "required": False,
                "default": 0,
            },
            {
                "table_slug": "catalogs",
                "name": "columns_json",
                "label": {"ru": "Столбцы", "en": "Columns"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "catalogs",
                "name": "error",
                "label": {"ru": "Ошибка", "en": "Error"},
                "type": "text",
                "required": False,
            },
            _project_ids_column("catalogs"),
            {
                "table_slug": "request_lines",
                "name": "title",
                "label": {"ru": "Название", "en": "Title"},
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "request_lines",
                "name": "part_number",
                "label": {"ru": "Партномер", "en": "Part number"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "request_lines",
                "name": "qty",
                "label": {"ru": "Кол-во", "en": "Qty"},
                "type": "number",
                "required": False,
                "default": 1,
            },
            {
                "table_slug": "request_lines",
                "name": "found_count",
                "label": {"ru": "Найдено", "en": "Found"},
                "type": "number",
                "required": False,
                "default": 0,
            },
            {
                "table_slug": "request_lines",
                "name": "selected_offer_id",
                "label": {"ru": "Выбранный оффер", "en": "Selected offer"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "request_lines",
                "name": "status",
                "label": {"ru": "Статус", "en": "Status"},
                "type": "enum",
                "required": True,
                "default": "open",
                "enum": {
                    "values": ["open", "matched", "selected"],
                    "labels": {"open": "Открыта", "matched": "Есть кандидаты", "selected": "Выбрано"},
                },
            },
            _project_ids_column("request_lines"),
            {
                "table_slug": "found_offers",
                "name": "line_id",
                "label": {"ru": "Позиция", "en": "Line"},
                "type": "ref",
                "required": False,
                "ref": {"table_slug": "request_lines"},
            },
            {
                "table_slug": "found_offers",
                "name": "title",
                "label": {"ru": "Товар", "en": "Title"},
                "type": "text",
                "required": True,
            },
            {
                "table_slug": "found_offers",
                "name": "part_number",
                "label": {"ru": "Партномер", "en": "Part number"},
                "type": "text",
                "required": False,
            },
            {
                "table_slug": "found_offers",
                "name": "price",
                "label": {"ru": "Цена", "en": "Price"},
                "type": "number",
                "required": False,
            },
            {
                "table_slug": "found_offers",
                "name": "catalog_id",
                "label": {"ru": "БД", "en": "Catalog"},
                "type": "ref",
                "required": False,
                "ref": {"table_slug": "catalogs"},
            },
            {
                "table_slug": "found_offers",
                "name": "score",
                "label": {"ru": "Релевантность", "en": "Score"},
                "type": "number",
                "required": False,
            },
            {
                "table_slug": "found_offers",
                "name": "match_kind",
                "label": {"ru": "Совпадение", "en": "Match"},
                "type": "enum",
                "required": False,
                "default": "analog",
                "enum": {
                    "values": ["exact", "analog"],
                    "labels": {"exact": "Точное", "analog": "Аналог"},
                },
            },
            {
                "table_slug": "found_offers",
                "name": "is_selected",
                "label": {"ru": "Выбран", "en": "Selected"},
                "type": "bool",
                "required": False,
                "default": False,
            },
            {
                "table_slug": "found_offers",
                "name": "source_title",
                "label": {"ru": "Запрос", "en": "Request title"},
                "type": "text",
                "required": False,
            },
            _project_ids_column("found_offers"),
        ],
        "views": [
            {
                "slug": "equipment_hub",
                "table_slug": "catalogs",
                "kind": "hub",
                "ui_json": {
                    "version": 1,
                    "kind": "hub",
                    "items": [
                        {
                            "title": "Базы данных",
                            "icon": "storage",
                            "target": {"kind": "view", "view": "catalogs_list"},
                        },
                        {
                            "title": "Позиции заказчика",
                            "icon": "list_alt",
                            "target": {"kind": "view", "view": "request_lines_list"},
                        },
                        {
                            "title": "Найденные товары",
                            "icon": "inventory_2",
                            "target": {"kind": "view", "view": "found_offers_list"},
                        },
                    ],
                },
            },
            {
                "slug": "catalogs_list",
                "table_slug": "catalogs",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {"title": {"ru": "Базы данных", "en": "Databases"}},
                    "title_field": "name",
                    "subtitle_fields": ["status", "row_count"],
                    "columns": [
                        {"field": "name", "label": {"ru": "Имя", "en": "Name"}},
                        {"field": "status", "label": {"ru": "Статус", "en": "Status"}},
                        {"field": "row_count", "label": {"ru": "Строк", "en": "Rows"}},
                    ],
                    "row_tap": {"kind": "open_view", "view": "catalogs_settings"},
                    "inline_add": {"field": "name", "title": "Добавить базу"},
                    "empty": _empty("Нет баз", "No databases"),
                },
            },
            {
                "slug": "catalogs_settings",
                "table_slug": "catalogs",
                "kind": "detail",
                "ui_json": {
                    "version": 1,
                    "kind": "detail",
                    "mode": "edit",
                    "title": {"ru": "Настройки БД", "en": "Database settings"},
                    "fields": [
                        {"column": "name", "widget": "value"},
                        {
                            "column": "source_file",
                            "widget": "file_upload",
                            "accept": ".csv,.xlsx,.xls",
                            "subtitle_from": "row_count",
                            "empty_style": "warning",
                        },
                        {"column": "status", "widget": "choice"},
                        {"column": "row_count", "widget": "value"},
                        {"column": "columns_json", "widget": "value"},
                        {"column": "error", "widget": "value"},
                        {"column": "project_ids", "widget": "project_multiselect"},
                    ],
                },
            },
            {
                "slug": "request_lines_list",
                "table_slug": "request_lines",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "Позиции заказчика", "en": "Request lines"}
                    },
                    "title_field": "title",
                    "subtitle_fields": ["part_number", "found_count"],
                    "columns": [
                        {"field": "title", "label": {"ru": "Название", "en": "Title"}},
                        {"field": "part_number", "label": {"ru": "Партномер", "en": "P/N"}},
                        {"field": "qty", "label": {"ru": "Кол-во", "en": "Qty"}},
                        {"field": "found_count", "label": {"ru": "Найдено", "en": "Found"}},
                        {"field": "status", "label": {"ru": "Статус", "en": "Status"}},
                    ],
                    "row_tap": {"kind": "open_view", "view": "offers_for_line"},
                    "inline_add": {"field": "title", "title": "Добавить позицию"},
                    "empty": _empty("Нет позиций", "No lines"),
                },
            },
            {
                "slug": "request_lines_form",
                "table_slug": "request_lines",
                "kind": "form",
                "ui_json": {
                    "version": 1,
                    "kind": "form",
                    "mode": "edit",
                    "title": {"ru": "Позиция", "en": "Line"},
                    "fields": [
                        {"column": "title", "widget": "value"},
                        {"column": "part_number", "widget": "value"},
                        {"column": "qty", "widget": "value"},
                        {"column": "project_ids", "widget": "project_multiselect"},
                    ],
                },
            },
            {
                "slug": "offers_for_line",
                "table_slug": "found_offers",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "Офферы позиции", "en": "Line offers"}
                    },
                    "title_field": "title",
                    "subtitle_fields": ["part_number", "match_kind", "score"],
                    "columns": [
                        {"field": "title", "label": {"ru": "Товар", "en": "Title"}},
                        {"field": "part_number", "label": {"ru": "Партномер", "en": "P/N"}},
                        {"field": "price", "label": {"ru": "Цена", "en": "Price"}},
                        {"field": "match_kind", "label": {"ru": "Совпадение", "en": "Match"}},
                        {"field": "score", "label": {"ru": "Оценка", "en": "Score"}},
                    ],
                    "context_bind": {"line_id": "contextRowId"},
                    "selection": {
                        "kind": "single",
                        "field": "is_selected",
                        "action": "select_offer_primary",
                    },
                    "row_tap": {"kind": "open_form", "view": "found_offers_form"},
                    "inline_add": {"field": "title", "title": "Добавить товар"},
                    "empty": _empty("Нет кандидатов", "No offers"),
                },
            },
            {
                "slug": "found_offers_list",
                "table_slug": "found_offers",
                "kind": "collection",
                "ui_json": {
                    "version": 1,
                    "kind": "collection",
                    "scaffold": {
                        "title": {"ru": "Найденные товары", "en": "Found offers"}
                    },
                    "title_field": "title",
                    "subtitle_fields": ["source_title", "match_kind"],
                    "columns": [
                        {"field": "title", "label": {"ru": "Товар", "en": "Title"}},
                        {"field": "part_number", "label": {"ru": "Партномер", "en": "P/N"}},
                        {"field": "price", "label": {"ru": "Цена", "en": "Price"}},
                        {"field": "match_kind", "label": {"ru": "Совпадение", "en": "Match"}},
                        {"field": "score", "label": {"ru": "Оценка", "en": "Score"}},
                        {"field": "source_title", "label": {"ru": "Запрос", "en": "Request"}},
                    ],
                    "row_tap": {"kind": "open_form", "view": "found_offers_form"},
                    "inline_add": {"field": "title", "title": "Добавить товар"},
                    "empty": _empty("Нет товаров", "No offers"),
                },
            },
            {
                "slug": "found_offers_form",
                "table_slug": "found_offers",
                "kind": "form",
                "ui_json": {
                    "version": 1,
                    "kind": "form",
                    "mode": "edit",
                    "title": {"ru": "Найденный товар", "en": "Offer"},
                    "fields": [
                        {"column": "title", "widget": "value"},
                        {"column": "line_id", "widget": "ref"},
                        {"column": "part_number", "widget": "value"},
                        {"column": "price", "widget": "value"},
                        {"column": "catalog_id", "widget": "ref"},
                        {"column": "score", "widget": "value"},
                        {"column": "match_kind", "widget": "choice"},
                        {"column": "is_selected", "widget": "switch"},
                        {"column": "source_title", "widget": "value"},
                        {"column": "project_ids", "widget": "project_multiselect"},
                    ],
                },
            },
        ],
        "tabs": [
            {
                "id": "tab_equipment",
                "title": "Подбор техники",
                "subtitle": "Каталоги, позиции и офферы",
                "order": 10,
                "icon": "precision_manufacturing",
                "view_slug": "equipment_hub",
                "table_slug": "catalogs",
                "enabled": True,
                "nav": {"contour": "employee", "placement": "data"},
            }
        ],
        "actions": [
            {
                "id": "index_catalog_file",
                "label": {"ru": "Индексировать", "en": "Index"},
                "kind": "content.index_tabular",
                "enabled": True,
                "params": {
                    "table_slug": "catalogs",
                    "source_column": "source_file",
                    "artifact_column": "artifact_ref",
                    "status_column": "status",
                    "row_count_column": "row_count",
                    "columns_json_column": "columns_json",
                    "error_column": "error",
                },
                "trigger": {"on": ["row.created", "row.updated"], "async": True},
                "ui": {"placement": ["toolbar"], "icon": "sync"},
            },
            {
                "id": "select_offer_primary",
                "label": {"ru": "Выбрать", "en": "Select"},
                "kind": "data.select_row",
                "enabled": True,
                "params": {
                    "table_slug": "found_offers",
                    "select_field": "is_selected",
                    "group_by": "line_id",
                    "parent": {
                        "table_slug": "request_lines",
                        "id_from": "line_id",
                        "set_field": "selected_offer_id",
                    },
                },
                "ui": {"placement": ["row_action"]},
            },
        ],
        "materialize": [
            {
                "id": "catalog_sqlite_artifacts",
                "enabled": True,
                "when": ["project.created", "project.resumed", "project.sync"],
                "priority": 40,
                "source": {
                    "type": "rows",
                    "table_slug": "catalogs",
                    "filter": {"status": "ready"},
                },
                "target": {
                    "workspace_path": "catalogs/{{row_id}}.sqlite",
                    "format": "copy_blob",
                    "field": "artifact_ref",
                },
            },
            {
                "id": "catalog_manifest",
                "enabled": True,
                "when": ["project.created", "project.resumed", "project.sync"],
                "priority": 41,
                "source": {
                    "type": "rows",
                    "table_slug": "catalogs",
                    "filter": {"status": "ready"},
                },
                "target": {
                    "workspace_path": "catalogs/manifest.json",
                    "format": "json_rows",
                },
            },
        ],
        "mcp_tools": [
            {
                "id": "equipment_catalog_list",
                "name": "equipment_catalog_list",
                "label": "List equipment catalogs",
                "description": "List ready catalog cards (Postgres SoT); files live at /workspace/catalogs",
                "enabled": True,
                "kind": "rows_query",
                "params_schema": {"type": "object", "properties": {}},
                "implementation": {
                    "table_slug": "catalogs",
                    "query": {"filter": {"status": "ready"}, "limit": 100},
                },
            },
            {
                "id": "equipment_catalog_query",
                "name": "equipment_catalog_query",
                "label": "Query catalog SQLite",
                "description": (
                    "RO search in materialized catalogs/*.sqlite (table rows). "
                    "Prefer part_number exact; else text LIKE across columns."
                ),
                "enabled": True,
                "kind": "workspace_sqlite_query",
                "params_schema": {
                    "type": "object",
                    "properties": {
                        "catalog_id": {"type": "string"},
                        "part_number": {"type": "string"},
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 20},
                    },
                },
                "implementation": {
                    "workspace_glob": "catalogs/*.sqlite",
                    "table": "rows",
                    "helper": "prodavan.application.modules.catalog_sqlite_query",
                },
            },
            {
                "id": "equipment_offers_upsert",
                "name": "equipment_offers_upsert",
                "label": "Upsert found offers",
                "description": (
                    "Agent writes candidates into found_offers (Postgres SoT) "
                    "and updates request_lines.found_count — never write offers only into Pod FS"
                ),
                "enabled": True,
                "kind": "rows_upsert",
                "implementation": {"table_slug": "found_offers"},
            },
        ],
        "seed_rows": {"items": []},
    }


PRODUCT_MODULES: list[tuple[str, str, dict[str, Any]]] = [
    ("mod_prompts", "Промпты", mod_prompts_meta()),
    ("mod_mcp", "MCP", mod_mcp_meta()),
    ("mod_files", "Файлы", mod_files_meta()),
    ("mod_equipment", "Подбор техники", mod_equipment_meta()),
]

EXAMPLE_MODULE_IDS: tuple[str, ...] = (
    "mod_example_suppliers",
    "mod_example_notes",
    "mod_example_hub",
    "mod_example_sku",
)
