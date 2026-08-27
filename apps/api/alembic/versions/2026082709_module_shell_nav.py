"""Add shell nav entries to example module tabs."""

import json

import sqlalchemy as sa
from alembic import op

revision = "2026082709"
down_revision = "2026082708"
branch_labels = None
depends_on = None

_TAB_PATCHES: dict[str, list[dict]] = {
    "mod_example_suppliers": [
        {
            "id": "tab_suppliers",
            "title": "Поставщики",
            "order": 150,
            "icon": "local_shipping_outlined",
            "view_slug": "suppliers_list",
            "table_slug": "suppliers",
            "enabled": True,
            "nav": {"contour": "admin"},
        }
    ],
    "mod_example_notes": [
        {
            "id": "tab_notes",
            "title": "Заметки",
            "order": 160,
            "icon": "note_outlined",
            "view_slug": "notes_form",
            "table_slug": "notes",
            "enabled": True,
            "nav": {"contour": "admin"},
        }
    ],
    "mod_example_hub": [
        {
            "id": "tab_hub",
            "title": "Hub меню",
            "order": 170,
            "icon": "apps_outlined",
            "view_slug": "main_hub",
            "enabled": True,
            "nav": {"contour": "admin"},
        }
    ],
}


def upgrade() -> None:
    conn = op.get_bind()
    for module_id, tabs in _TAB_PATCHES.items():
        conn.execute(
            sa.text(
                """
                UPDATE module_meta_documents
                SET body = CAST(:body AS jsonb)
                WHERE module_id = :module_id AND slug = 'tabs'
                """
            ),
            {"module_id": module_id, "body": json.dumps(tabs)},
        )


def downgrade() -> None:
    conn = op.get_bind()
    # Restore pre-shell tabs from seed migration 2026082704.
    legacy = {
        "mod_example_suppliers": [
            {
                "id": "tab_suppliers",
                "title": "Поставщики",
                "order": 100,
                "view_slug": "suppliers_list",
                "table_slug": "suppliers",
                "enabled": True,
            }
        ],
        "mod_example_notes": [
            {
                "id": "tab_notes",
                "title": "Заметка",
                "order": 10,
                "view_slug": "notes_form",
                "table_slug": "notes",
                "enabled": True,
            }
        ],
        "mod_example_hub": [
            {
                "id": "tab_hub",
                "title": "Меню",
                "order": 1,
                "view_slug": "main_hub",
                "enabled": True,
            }
        ],
    }
    for module_id, tabs in legacy.items():
        conn.execute(
            sa.text(
                """
                UPDATE module_meta_documents
                SET body = CAST(:body AS jsonb)
                WHERE module_id = :module_id AND slug = 'tabs'
                """
            ),
            {"module_id": module_id, "body": json.dumps(tabs)},
        )
