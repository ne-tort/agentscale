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

_BLOCK_LABELS = {
    "rules": "Rules",
    "skills": "Skills",
    "output_schema": "Output Schema",
    "guardrails": "Guardrails",
    "examples": "Examples",
    "others": "Others",
}

_BLOCK_INLINE_TITLES = {
    "rules": "Добавить правило",
    "skills": "Добавить skill",
    "output_schema": "Добавить output schema",
    "guardrails": "Добавить guardrail",
    "examples": "Добавить example",
    "others": "Добавить запись",
}


def _collection_view(
    *,
    slug: str,
    table_slug: str,
    form_slug: str,
    title_field: str = "name",
    label: str = "Имя",
    inline_title: str = "Добавить запись",
) -> dict[str, Any]:
    return {
        "slug": slug,
        "table_slug": table_slug,
        "kind": "collection",
        "ui_json": {
            "version": 1,
            "kind": "collection",
            "title_field": title_field,
            "subtitle_fields": [],
            "columns": [{"field": title_field, "label": label}],
            "row_tap": {"kind": "open_form", "view": form_slug},
            "inline_add": {"field": title_field, "title": inline_title},
        },
    }


def _markdown_form(*, slug: str, table_slug: str, title: str) -> dict[str, Any]:
    return {
        "slug": slug,
        "table_slug": table_slug,
        "kind": "form",
        "ui_json": {
            "version": 1,
            "kind": "form",
            "mode": "edit",
            "title": title,
            "fields": [
                {"column": "name", "widget": "value"},
                {"column": "body_md", "widget": "markdown_editor"},
            ],
        },
    }


def _prompt_item_views(block_type: str) -> tuple[dict[str, Any], dict[str, Any]]:
    list_slug = f"{block_type}_list"
    form_slug = f"{block_type}_form"
    label = _BLOCK_LABELS.get(block_type, block_type)
    inline_title = _BLOCK_INLINE_TITLES.get(block_type, f"Добавить {label.lower()}")
    coll = _collection_view(
        slug=list_slug,
        table_slug="prompt_items",
        form_slug=form_slug,
        label=label,
        inline_title=inline_title,
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
            "when": ["project.created", "project.resumed"],
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
                "when": ["project.created", "project.resumed"],
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
        ],
        "views": views,
        "tabs": [
            {
                "id": "tab_prompts",
                "title": "Промпты",
                "order": 10,
                "icon": "psychology_outlined",
                "view_slug": "prompts_hub",
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
                    "body": {"name": "Default", "is_default": True},
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
        ],
        "views": [
            _collection_view(
                slug="files_list",
                table_slug="files",
                form_slug="files_form",
                label="Имя",
                inline_title="Добавить файл",
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
                    ],
                },
            },
        ],
        "tabs": [
            {
                "id": "tab_files",
                "title": "Файлы",
                "order": 30,
                "icon": "folder_outlined",
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
                "when": ["project.created", "project.resumed"],
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
        ],
        "views": [
            _collection_view(
                slug="mcp_packages_list",
                table_slug="mcp_packages",
                form_slug="mcp_packages_form",
                inline_title="Добавить MCP package",
            ),
            {
                "slug": "mcp_packages_form",
                "table_slug": "mcp_packages",
                "kind": "form",
                "ui_json": {
                    "version": 1,
                    "kind": "form",
                    "mode": "edit",
                    "title": "MCP package",
                    "fields": [
                        {"column": "name", "widget": "value"},
                        {"column": "version", "widget": "value"},
                        {"column": "enabled", "widget": "switch"},
                        {"column": "file_ref", "widget": "file_upload", "accept": ".zip"},
                    ],
                },
            },
        ],
        "tabs": [
            {
                "id": "tab_mcp",
                "title": "MCP",
                "order": 20,
                "icon": "extension_outlined",
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
                "when": ["project.created", "project.resumed"],
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


PRODUCT_MODULES: list[tuple[str, str, dict[str, Any]]] = [
    ("mod_prompts", "Промпты", mod_prompts_meta()),
    ("mod_mcp", "MCP", mod_mcp_meta()),
    ("mod_files", "Файлы", mod_files_meta()),
]

EXAMPLE_MODULE_IDS: tuple[str, ...] = (
    "mod_example_suppliers",
    "mod_example_notes",
    "mod_example_hub",
    "mod_example_sku",
)
