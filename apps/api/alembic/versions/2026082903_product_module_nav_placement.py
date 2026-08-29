"""Patch product module tabs with employee nav.placement."""

import json

import sqlalchemy as sa
from alembic import op

revision = "2026082903"
down_revision = "2026082902"
branch_labels = None
depends_on = None

_NAV = {"contour": "employee", "placement": "management"}

_TAB_PATCHES: dict[str, list[dict]] = {
    "mod_prompts": [
        {
            "id": "tab_prompts",
            "title": "Промпты",
            "order": 10,
            "icon": "psychology_outlined",
            "view_slug": "prompts_hub",
            "enabled": True,
            "nav": _NAV,
        }
    ],
    "mod_mcp": [
        {
            "id": "tab_mcp",
            "title": "MCP",
            "order": 20,
            "icon": "extension_outlined",
            "view_slug": "mcp_packages_list",
            "table_slug": "mcp_packages",
            "enabled": True,
            "nav": _NAV,
        }
    ],
    "mod_files": [
        {
            "id": "tab_files",
            "title": "Файлы",
            "order": 30,
            "icon": "folder_outlined",
            "view_slug": "files_list",
            "table_slug": "files",
            "enabled": True,
            "nav": _NAV,
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
    legacy = {
        "mod_prompts": [
            {
                "id": "tab_prompts",
                "title": "Промпты",
                "order": 10,
                "icon": "psychology_outlined",
                "view_slug": "prompts_hub",
                "enabled": True,
            }
        ],
        "mod_mcp": [
            {
                "id": "tab_mcp",
                "title": "MCP",
                "order": 20,
                "icon": "extension_outlined",
                "view_slug": "mcp_packages_list",
                "table_slug": "mcp_packages",
                "enabled": True,
            }
        ],
        "mod_files": [
            {
                "id": "tab_files",
                "title": "Файлы",
                "order": 30,
                "icon": "folder_outlined",
                "view_slug": "files_list",
                "table_slug": "files",
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
