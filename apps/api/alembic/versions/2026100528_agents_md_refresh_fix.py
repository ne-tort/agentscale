"""Исправление 2026100527: AGENTS.md «Подбора техники» доходит до инстансов.

В `2026100527` проверка «кастомизирован ли системный промпт» читала
`body["body"]`, но у строки `equipment_prompts_agents_default` текста на верхнем
уровне нет — он лежит в `files_json[name == "AGENTS.md"].body`. Пустая строка не
начиналась со стокового заголовка, поэтому все три инстанса попали в ветку
«похоже на кастомизацию, не трогаем», и AGENTS.md остался прежним (5886 символов
вместо актуальных 8471): без каталога «Готовые сборки» и без указателя на
`70-ready-builds.md`. Правила при этом дописались корректно.

Логика та же, что задумывалась: замена только когда промпт явно не
кастомизирован (стоковый заголовок и ещё нет маркера актуальности), иначе
warning и пропуск. Идемпотентно.
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa

from alembic import op
from prodavan.application.platform.equipment_prompt_content import EQUIPMENT_AGENTS_MD

logger = logging.getLogger(__name__)

revision = "2026100528"
down_revision = "2026100527"

_AGENTS_ROW_ID = "equipment_prompts_agents_default"
_AGENTS_FILE_NAME = "AGENTS.md"
_AGENTS_HEADER = "# Prodavan — агент подбора техники"
_AGENTS_MARKER = "ready_builds_catalog"


def _refresh_agents_body(body: dict) -> tuple[dict | None, str]:
    """Возвращает (новое тело, причина). None — менять нечего."""
    files = body.get("files_json")
    entries = [dict(f) for f in files if isinstance(f, dict)] if isinstance(files, list) else []
    entry = next(
        (f for f in entries if str(f.get("name") or "") == _AGENTS_FILE_NAME), None
    )
    if entry is None:
        # строка без файла AGENTS.md — не наша ответственность
        return None, "no_agents_file"
    text = str(entry.get("body") or "")
    if _AGENTS_MARKER in text:
        return None, "already_current"
    if not text.lstrip().startswith(_AGENTS_HEADER):
        return None, "customized"
    entry["body"] = EQUIPMENT_AGENTS_MD
    return {**body, "files_json": entries}, "updated"


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            """
            SELECT id, instance_id, body
            FROM module_instance_data_rows
            WHERE table_slug = 'equipment_prompts' AND row_id = :rid
            """
        ),
        {"rid": _AGENTS_ROW_ID},
    ).fetchall()

    updated = 0
    customized = 0
    for row in rows:
        body = row[2]
        if isinstance(body, str):
            body = json.loads(body)
        if not isinstance(body, dict):
            continue
        new_body, reason = _refresh_agents_body(body)
        if new_body is None:
            if reason == "customized":
                customized += 1
                logger.warning(
                    "equipment_prompts AGENTS.md customized (instance=%s) — left as is; "
                    "add the «Готовые сборки» pointer manually",
                    row[1],
                )
            continue
        conn.execute(
            sa.text(
                "UPDATE module_instance_data_rows SET body = CAST(:body AS jsonb) WHERE id = :id"
            ),
            {"body": json.dumps(new_body, ensure_ascii=False), "id": row[0]},
        )
        updated += 1

    logger.info(
        "AGENTS.md refresh: updated=%s customized_skipped=%s total=%s",
        updated,
        customized,
        len(rows),
    )


def downgrade() -> None:
    pass
