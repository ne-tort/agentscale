"""WAVE17: B2B tender module — structured fields, lifecycle/work statuses.

Metadata only (tenders live in module JSONB rows), so this migration upserts
the product module seeds and backfills the two new status fields for existing
tender rows. No DDL.
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op
from prodavan.application.platform.equipment_prompt_content import (
    EQUIPMENT_AGENTS_MD,
    EQUIPMENT_RULES_FILES,
)
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026103001"
down_revision = "2026102301"


def _rules_payload() -> list[dict]:
    return [
        {"id": f"rules_default_{name}", "name": name, "priority": priority, "body": body}
        for name, body, priority in EQUIPMENT_RULES_FILES
    ]


def _agents_payload() -> list[dict]:
    return [
        {
            "id": "agents_md_default",
            "name": "AGENTS.md",
            "priority": 10,
            "body": EQUIPMENT_AGENTS_MD,
        }
    ]


def upgrade() -> None:
    upsert_product_modules(op.get_bind())
    op.execute(
        """
        UPDATE module_instance_data_rows
        SET body = body
            || jsonb_build_object(
                'work_status',
                CASE lower(coalesce(body->>'work_status', body->>'status', 'draft'))
                    WHEN 'active' THEN 'in_progress'
                    WHEN 'submitted' THEN 'in_progress'
                    WHEN 'won' THEN 'processed'
                    WHEN 'lost' THEN 'processed'
                    WHEN 'cancelled' THEN 'invalid'
                    WHEN 'irrelevant' THEN 'invalid'
                    WHEN 'in_progress' THEN 'in_progress'
                    WHEN 'processed' THEN 'processed'
                    WHEN 'invalid' THEN 'invalid'
                    WHEN 'on_hold' THEN 'on_hold'
                    ELSE 'new'
                END,
                'lifecycle_status',
                CASE
                    WHEN body->>'deadline_at' IS NULL OR body->>'deadline_at' = '' THEN 'active'
                    WHEN (body->>'deadline_at')::timestamptz <= now() THEN 'inactive'
                    ELSE 'active'
                END
            )
        WHERE table_slug = 'tenders'
          AND (body->>'work_status' IS NULL OR body->>'lifecycle_status' IS NULL)
        """
    )
    _refresh_prompts()
    op.execute(
        """
        UPDATE module_instance_data_rows
        SET body = jsonb_set(body, '{version}', '"3.3.0"'::jsonb, true)
        WHERE table_slug = 'equipment_mcp'
          AND row_id = 'equipment_mcp_default'
        """
    )


def _refresh_prompts() -> None:
    """Existing instances keep seed rows untouched; refresh the two templates."""
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            """
            SELECT id, row_id, body FROM module_instance_data_rows
            WHERE table_slug = 'equipment_prompts'
              AND row_id IN ('equipment_prompts_agents_default', 'equipment_prompts_rules_default')
            """
        )
    ).fetchall()
    for row in rows:
        body = row[2]
        if isinstance(body, str):
            body = json.loads(body)
        if not isinstance(body, dict):
            continue
        body = dict(body)
        body["files_json"] = (
            _agents_payload()
            if str(row[1]) == "equipment_prompts_agents_default"
            else _rules_payload()
        )
        conn.execute(
            sa.text("UPDATE module_instance_data_rows SET body = CAST(:body AS jsonb) WHERE id = :id"),
            {"body": json.dumps(body, ensure_ascii=False), "id": row[0]},
        )


def downgrade() -> None:
    pass
