"""WAVE11: довести промпты «Подбора техники» до существующих инстансов.

`_apply_seed_rows_to_instances` вставляет seed-строки через
`ON CONFLICT DO NOTHING` — это правильно для пользовательских данных, но
из-за этого шаблонные строки `equipment_prompts` НИКОГДА не обновляются.
В результате правила `60-builds.md` (WAVE10) и `70-ready-builds.md` (WAVE11)
и новый `AGENTS.md` не доезжали до существующих проектов: агент физически не
знал ни про сборки, ни про каталог готовых сборок.

Стратегия — аддитивная, пользовательские правки не затираются:

- строка правил (`prompts/equipment`): ДОПИСАТЬ недостающие файлы из текущего
  сида (по имени); уже существующие записи не трогаются вовсе, поэтому
  отредактированный пользователем текст правила сохраняется;
- строка `AGENTS.md`: заменяется на актуальный сид только если она явно не
  кастомизирована (заголовок стоковый и нового маркера в тексте ещё нет).
  Если заголовок изменён — оставляем как есть и пишем warning в лог:
  системный промпт трогать опаснее, чем недодать указатель.

Идемпотентно: повторный прогон ничего не меняет.
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

revision = "2026100527"
down_revision = "2026100526"

_AGENTS_ROW_ID = "equipment_prompts_agents_default"
_RULES_ROW_ID = "equipment_prompts_rules_default"
_AGENTS_HEADER = "# Prodavan — агент подбора техники"
# маркер актуальности системного промпта: указатель на каталог готовых сборок
_AGENTS_MARKER = "ready_builds_catalog"


def _merge_rules_files(existing: list) -> tuple[list, list[str]]:
    """Дописывает недостающие файлы правил, существующие не трогает."""
    items = [dict(f) for f in existing if isinstance(f, dict)]
    present = {str(f.get("name") or "") for f in items}
    added: list[str] = []
    for name, body, priority in EQUIPMENT_RULES_FILES:
        if name in present:
            continue
        items.append({"id": f"rules_default_{name}", "name": name,
                      "priority": priority, "body": body})
        added.append(name)
    items.sort(key=lambda f: (f.get("priority") or 0, str(f.get("name") or "")))
    return items, added


def upgrade() -> None:
    conn = op.get_bind()
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
    agents_skipped = 0
    for row in rows:
        body = row[3]
        if isinstance(body, str):
            body = json.loads(body)
        if not isinstance(body, dict):
            continue
        body = dict(body)
        row_id = str(row[2])

        if row_id == _RULES_ROW_ID:
            existing = body.get("files_json")
            merged, added = _merge_rules_files(
                existing if isinstance(existing, list) else []
            )
            if not added:
                continue
            body["files_json"] = merged
            rules_updated += 1
            logger.info(
                "equipment_prompts rules +%s (instance=%s)", added, row[1]
            )
        elif row_id == _AGENTS_ROW_ID:
            text = str(body.get("body") or "")
            if _AGENTS_MARKER in text:
                continue  # уже актуален
            if not text.lstrip().startswith(_AGENTS_HEADER):
                # заголовок изменён — похоже на кастомизацию, не трогаем
                agents_skipped += 1
                logger.warning(
                    "equipment_prompts AGENTS.md customized (instance=%s) — "
                    "left as is; add the «Готовые сборки» pointer manually",
                    row[1],
                )
                continue
            files = body.get("files_json")
            entries = [dict(f) for f in files if isinstance(f, dict)] if isinstance(files, list) else []
            for entry in entries:
                if str(entry.get("name") or "") == "AGENTS.md":
                    entry["body"] = EQUIPMENT_AGENTS_MD
            body["files_json"] = entries or [
                {"id": "agents_md_default", "name": "AGENTS.md",
                 "priority": 10, "body": EQUIPMENT_AGENTS_MD}
            ]
            agents_updated += 1

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
        "equipment_prompts refresh: rules=%s agents=%s skipped_customized=%s",
        rules_updated,
        agents_updated,
        agents_skipped,
    )


def downgrade() -> None:
    pass
