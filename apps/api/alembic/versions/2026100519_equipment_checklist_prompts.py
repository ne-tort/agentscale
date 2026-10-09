"""Чеклист позиций в промптах «Подбора техники» + убрать легаси-«КП».

Агент теперь обязан вести чеклист по всем позициям заявки (todo.write),
помечая состояния и — по делу — цвета/комментарии, чтобы менеджер видел
прогресс в чате. Заодно из промптов убрана легаси-формулировка про «КП»
как artefact, который собирает агент (документы выгружает менеджер).

Сид-строки equipment_prompts вставлялись с ON CONFLICT DO NOTHING — новые
тела не доехали бы до существующих инстансов. Обновляем только строки,
созданные сидом (created_by='module_seed').
"""

import json

import sqlalchemy as sa

from alembic import op
from prodavan.application.platform.product_module_seeds import (
    _equipment_prompt_seed_rows,
)

revision = "2026100519"
down_revision = "2026100518"

_SEED_ROW_IDS = ("equipment_prompts_agents_default", "equipment_prompts_rules_default")


def upgrade() -> None:
    conn = op.get_bind()
    bodies = {row["row_id"]: row["body"] for row in _equipment_prompt_seed_rows()}
    for row_id, body in bodies.items():
        if row_id not in _SEED_ROW_IDS:
            continue
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
