"""Seed example module catalog entries with demo manifests."""

import json

import sqlalchemy as sa
from alembic import op

revision = "2026082704"
down_revision = "2026082703"
branch_labels = None
depends_on = None

_EXAMPLE_MODULES: list[tuple[str, str, dict[str, list]]] = [
    (
        "mod_example_suppliers",
        "Suppliers Pack",
        {
            "tables": [
                {
                    "slug": "suppliers",
                    "label": "Поставщики",
                    "storage_kind": "json_document",
                    "enabled": True,
                    "scope": {"projects": "all"},
                }
            ],
            "columns": [
                {
                    "table_slug": "suppliers",
                    "name": "name",
                    "label": "Имя",
                    "type": "text",
                    "required": True,
                },
                {
                    "table_slug": "suppliers",
                    "name": "status",
                    "label": "Статус",
                    "type": "enum",
                    "required": True,
                    "default": "active",
                    "enum": {
                        "values": ["active", "blocked"],
                        "labels": {"active": "Активен", "blocked": "Заблокирован"},
                    },
                },
                {
                    "table_slug": "suppliers",
                    "name": "region",
                    "label": "Регион",
                    "type": "text",
                    "required": False,
                },
            ],
            "views": [
                {
                    "slug": "suppliers_list",
                    "table_slug": "suppliers",
                    "kind": "collection",
                    "ui_json": {
                        "version": 1,
                        "kind": "collection",
                        "title_field": "name",
                        "subtitle_fields": ["status", "region"],
                        "columns": [
                            {"field": "name", "label": "Имя"},
                            {"field": "status", "label": "Статус"},
                            {"field": "region", "label": "Регион"},
                        ],
                        "primary_action": {"kind": "create_row", "label": "Добавить"},
                        "row_tap": {"kind": "open_form", "view": "suppliers_form"},
                    },
                },
                {
                    "slug": "suppliers_form",
                    "table_slug": "suppliers",
                    "kind": "form",
                    "ui_json": {
                        "version": 1,
                        "kind": "form",
                        "mode": "edit",
                        "fields": [
                            {"column": "name", "widget": "value"},
                            {"column": "status", "widget": "choice"},
                            {"column": "region", "widget": "value"},
                        ],
                    },
                },
            ],
            "tabs": [
                {
                    "id": "tab_suppliers",
                    "title": "Поставщики",
                    "order": 100,
                    "view_slug": "suppliers_list",
                    "table_slug": "suppliers",
                    "enabled": True,
                }
            ],
        },
    ),
    (
        "mod_example_notes",
        "Notes",
        {
            "tables": [
                {
                    "slug": "notes",
                    "label": "Заметки",
                    "storage_kind": "json_document",
                    "enabled": True,
                    "scope": {"projects": "all"},
                }
            ],
            "columns": [
                {
                    "table_slug": "notes",
                    "name": "title",
                    "label": "Заголовок",
                    "type": "text",
                    "required": True,
                },
                {
                    "table_slug": "notes",
                    "name": "body",
                    "label": "Текст",
                    "type": "text",
                    "required": False,
                },
                {
                    "table_slug": "notes",
                    "name": "pinned",
                    "label": "Закреплено",
                    "type": "bool",
                    "required": False,
                    "default": False,
                },
            ],
            "views": [
                {
                    "slug": "notes_form",
                    "table_slug": "notes",
                    "kind": "form",
                    "ui_json": {
                        "version": 1,
                        "kind": "form",
                        "mode": "edit",
                        "fields": [
                            {"column": "title", "widget": "value"},
                            {"column": "body", "widget": "value"},
                            {"column": "pinned", "widget": "switch"},
                        ],
                    },
                }
            ],
            "tabs": [
                {
                    "id": "tab_notes",
                    "title": "Заметка",
                    "order": 10,
                    "view_slug": "notes_form",
                    "table_slug": "notes",
                    "enabled": True,
                }
            ],
        },
    ),
    (
        "mod_example_hub",
        "Hub demo",
        {
            "tables": [
                {
                    "slug": "items",
                    "label": "Items",
                    "storage_kind": "json_document",
                    "enabled": True,
                    "scope": {"projects": "all"},
                }
            ],
            "columns": [
                {
                    "table_slug": "items",
                    "name": "name",
                    "label": "Имя",
                    "type": "text",
                    "required": True,
                }
            ],
            "views": [
                {
                    "slug": "main_hub",
                    "kind": "hub",
                    "ui_json": {
                        "version": 1,
                        "kind": "hub",
                        "items": [
                            {
                                "title": "Список",
                                "icon": "list",
                                "target": {"kind": "view", "view": "items_list"},
                            },
                            {
                                "title": "Настройки",
                                "icon": "settings",
                                "target": {"kind": "stub"},
                            },
                        ],
                    },
                },
                {
                    "slug": "items_list",
                    "table_slug": "items",
                    "kind": "collection",
                    "ui_json": {
                        "version": 1,
                        "kind": "collection",
                        "title_field": "name",
                        "columns": [{"field": "name", "label": "Имя"}],
                    },
                },
            ],
            "tabs": [
                {
                    "id": "tab_hub",
                    "title": "Меню",
                    "order": 1,
                    "view_slug": "main_hub",
                    "enabled": True,
                }
            ],
        },
    ),
    (
        "mod_example_sku",
        "SKU map (data-only)",
        {
            "tables": [
                {
                    "slug": "sku_map",
                    "label": "SKU map",
                    "storage_kind": "json_document",
                    "enabled": True,
                    "scope": {"projects": "bound"},
                }
            ],
            "columns": [
                {
                    "table_slug": "sku_map",
                    "name": "sku",
                    "label": "SKU",
                    "type": "text",
                    "required": True,
                },
                {
                    "table_slug": "sku_map",
                    "name": "vendor",
                    "label": "Vendor",
                    "type": "text",
                    "required": True,
                },
                {
                    "table_slug": "sku_map",
                    "name": "meta",
                    "label": "Meta",
                    "type": "json",
                    "required": False,
                },
            ],
            "views": [],
            "tabs": [],
            "mcp_tools": [
                {
                    "id": "sku_lookup",
                    "name": "sku_lookup",
                    "kind": "rows_query",
                    "implementation": {
                        "table_slug": "sku_map",
                        "query": {
                            "filter": {"sku": {"op": "eq", "value": "{{sku}}"}},
                            "limit": 1,
                        },
                    },
                    "params_schema": {
                        "type": "object",
                        "properties": {"sku": {"type": "string"}},
                        "required": ["sku"],
                    },
                }
            ],
        },
    ),
]


def _insert_module(conn: sa.Connection, module_id: str, name: str, slugs: dict[str, list]) -> None:
    conn.execute(
        sa.text(
            """
            INSERT INTO modules (id, name, status)
            VALUES (:id, :name, 'active')
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"id": module_id, "name": name},
    )
    for slug, body in slugs.items():
        doc_id = f"mmd_{module_id.removeprefix('mod_')}_{slug}"
        conn.execute(
            sa.text(
                """
                INSERT INTO module_meta_documents (id, module_id, slug, body)
                VALUES (:id, :module_id, :slug, CAST(:body AS jsonb))
                ON CONFLICT (module_id, slug) DO NOTHING
                """
            ),
            {
                "id": doc_id,
                "module_id": module_id,
                "slug": slug,
                "body": json.dumps(body),
            },
        )


def upgrade() -> None:
    conn = op.get_bind()
    for module_id, name, slugs in _EXAMPLE_MODULES:
        _insert_module(conn, module_id, name, slugs)


def downgrade() -> None:
    conn = op.get_bind()
    for module_id, _, _ in reversed(_EXAMPLE_MODULES):
        conn.execute(
            sa.text("DELETE FROM modules WHERE id = :id"),
            {"id": module_id},
        )
