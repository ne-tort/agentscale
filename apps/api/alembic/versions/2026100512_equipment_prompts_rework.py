"""Пересмотр системных промптов «Подбора техники»: наличие прежде всего,
match_kind = уверенность в личности (не в наличии), кандидаты только из
хитов каталога; плюс face-фолбэк групп без офферов (equipment_offers_service).

Сид-строки equipment_prompts вставлялись ранее с ON CONFLICT DO NOTHING —
новые тела не доехали бы до существующих инстансов. Здесь строки, созданные
сидом (created_by='module_seed'), обновляются телом из актуальных сидов;
строки, заведённые пользователем, не трогаются.
"""

import json

import sqlalchemy as sa
from alembic import op

from prodavan.application.platform.product_module_seeds import (
    _equipment_prompt_seed_rows,
)

revision = "2026100512"
down_revision = "2026100511"

_SEED_ROW_IDS = ("equipment_prompts_agents_default", "equipment_prompts_rules_default")


def upgrade() -> None:
    conn = op.get_bind()
    bodies = {row["row_id"]: row["body"] for row in _equipment_prompt_seed_rows()}
    for row_id, body in bodies.items():
        conn.execute(
            sa.text(
                """
                UPDATE module_instance_data_rows
                SET body = CAST(:body AS jsonb)
                WHERE table_slug = 'equipment_prompts'
                  AND row_id = :rid
                  AND created_by = 'module_seed'
                """
            ),
            {"body": json.dumps(body, ensure_ascii=False), "rid": row_id},
        )


def downgrade() -> None:
    pass
