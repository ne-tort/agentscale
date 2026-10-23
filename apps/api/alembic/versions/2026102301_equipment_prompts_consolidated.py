"""Обновить промпты подбора до актуального сида (дефолтная ревизия).

Зачем отдельная миграция. Промпты живут строками данных модуля
(`module_instance_data_rows`, table_slug `equipment_prompts`), а не в коде.
Обновление дефолтов происходит только миграцией — и предыдущая (2026100527)
работала «аддитивно»:

* файлы правил она только ДОПИСЫВАЛА: если `10-request.md`…`70-ready-builds.md`
  уже есть, ничего не менялось. Поэтому новый `05-tenders.md` не появился ни на
  одном существующем инстансе;
* `AGENTS.md` обновлялся по маркеру `ready_builds_catalog`. Маркер стоит с
  прошлой ревизии → правки текста (чистка мусора, −35% объёма) не доехали.

Итог на проде перед этой миграцией: 7 файлов правил (без тендерного), AGENTS.md
10373 символов — старая пространная версия.

Эта миграция фиксирует НОВУЮ дефолтную ревизию: правила и AGENTS.md
перезаписываются из сида целиком. Это осознанно: см. PR «промпты подбора —
операционная документация вместо свалки оговорок». Правки, сделанные
пользователем в интерфейсе, будут заменены — предыдущее содержимое остаётся в
git-истории (`equipment_prompt_content.py`).

Идемпотентность: повторный прогон ничего не ломает (запись того же содержимого).
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from alembic import op

from prodavan.application.platform.equipment_prompt_content import (
    EQUIPMENT_AGENTS_MD,
    EQUIPMENT_RULES_FILES,
)
from prodavan.application.platform.product_module_upsert import upsert_product_modules

logger = logging.getLogger(__name__)

revision = "2026102301"
down_revision = "2026102202"

_AGENTS_ROW_ID = "equipment_prompts_agents_default"
_RULES_ROW_ID = "equipment_prompts_rules_default"


def _rules_files_payload() -> list[dict]:
    """Актуальный набор файлов правил в форме строки `files_json`."""
    return [
        {
            "id": f"rules_default_{name}",
            "name": name,
            "priority": priority,
            "body": body,
        }
        for name, body, priority in EQUIPMENT_RULES_FILES
    ]


def _agents_files_payload() -> list[dict]:
    """AGENTS.md в форме строки `files_json` (один файл в корне workspace)."""
    return [
        {
            "id": "agents_md_default",
            "name": "AGENTS.md",
            "priority": 10,
            "body": EQUIPMENT_AGENTS_MD,
        }
    ]


def upgrade() -> None:
    conn = op.get_bind()
    # Модуль и его дефолтные строки должны существовать (новые инстансы
    # создаются здесь же, чтобы миграция не зависела от порядка поставки).
    upsert_product_modules(conn)

    rows = conn.execute(
        sa.text(
            """
            SELECT id, instance_id, row_id, body
            FROM module_instance_data_rows
            WHERE table_slug = 'equipment_prompts'
              AND row_id IN (:agents, :rules)
            """
        ),
        {"agents": _AGENTS_ROW_ID, "rules": _RULES_ROW_ID},
    ).fetchall()

    rules_updated = 0
    agents_updated = 0

    for row in rows:
        body = row[3]
        if isinstance(body, str):
            body = json.loads(body)
        if not isinstance(body, dict):
            continue
        body = dict(body)
        row_id = str(row[2])

        if row_id == _RULES_ROW_ID:
            body["files_json"] = _rules_files_payload()
            rules_updated += 1
            logger.info(
                "equipment_prompts: правила обновлены до %s файлов (instance=%s)",
                len(EQUIPMENT_RULES_FILES),
                row[1],
            )
        elif row_id == _AGENTS_ROW_ID:
            body["files_json"] = _agents_files_payload()
            agents_updated += 1
            logger.info(
                "equipment_prompts: AGENTS.md обновлён (%s символов, instance=%s)",
                len(EQUIPMENT_AGENTS_MD),
                row[1],
            )
        else:  # pragma: no cover - защита от будущих row_id в том же IN
            continue

        conn.execute(
            sa.text(
                """
                UPDATE module_instance_data_rows
                SET body = CAST(:body AS jsonb)
                WHERE id = :id
                """
            ),
            {"body": json.dumps(body, ensure_ascii=False), "id": row[0]},
        )

    logger.info(
        "equipment_prompts refresh (2026102301): rules=%s agents=%s",
        rules_updated,
        agents_updated,
    )


def downgrade() -> None:
    """Необратимо по содержимому: предыдущий текст — в git-истории."""
    pass