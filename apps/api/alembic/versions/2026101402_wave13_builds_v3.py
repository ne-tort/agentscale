"""WAVE13: виды сборок + устройства-комплекты + сплит объёмов + количество связи.

- Новые типы-устройства (etype_laptop/nettop/monoblock/phone) — seed_rows с
  ON CONFLICT DO NOTHING: вставятся там, где их ещё нет.
- `etype_cooling.cooling_kind` стал groups (air/aio) — обновляем seed-строку.
- Мета: новые колонки (equipment_builds.qty), build_kind enum расширен, новые
  views (ready_build_spec), виджеты — через upsert_product_modules.
- Промпты переписаны (60-builds: виды, сплит, корпус+кулер, GPU по запросу) —
  принудительное обновление, как в 2026101401.

Данные не ломаются: новые таблицы не появляются, старые не удаляются.
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa

from alembic import op
from prodavan.application.platform.product_module_seeds import (
    _equipment_prompt_seed_rows,
    _equipment_type_seed_rows,
)
from prodavan.application.platform.product_module_upsert import upsert_product_modules

logger = logging.getLogger(__name__)

revision = "2026101402"
down_revision = "2026101401"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)

    # ------------------------------------------------------ шаблоны типов
    # обновляем ВСЕ seed-типы (новые device-типы создадутся, существующие
    # получат свежий fields_json — например etype_cooling.cooling_kind стал
    # groups air/aio). Пользовательские типы (created_by != module_seed) не
    # трогаем.
    type_bodies = {row["row_id"]: row["body"] for row in _equipment_type_seed_rows()}
    seed_rows = conn.execute(
        sa.text(
            "SELECT id, row_id, body FROM module_instance_data_rows "
            "WHERE table_slug = 'equipment_types' AND created_by = 'module_seed' "
            "AND row_id = ANY(:ids)"
        ),
        {"ids": list(type_bodies)},
    ).fetchall()
    updated = 0
    for row in seed_rows:
        body = row[2]
        if isinstance(body, str):
            body = json.loads(body)
        body = dict(body or {})
        want = type_bodies.get(str(row[1]))
        if not want:
            continue
        if body.get("fields_json") == want.get("fields_json"):
            continue
        body["fields_json"] = want.get("fields_json")
        body["name"] = want.get("name") or body.get("name")
        body["sort_order"] = want.get("sort_order", body.get("sort_order"))
        body["build_scope"] = want.get("build_scope", body.get("build_scope"))
        conn.execute(
            sa.text(
                "UPDATE module_instance_data_rows "
                "SET body = CAST(:body AS jsonb) WHERE id = :id"
            ),
            {"body": json.dumps(body, ensure_ascii=False), "id": row[0]},
        )
        updated += 1
    logger.info("wave13: equipment_types refreshed: %s", updated)

    # ------------------------------------------------------ промпты
    prompt_bodies = {row["row_id"]: row["body"] for row in _equipment_prompt_seed_rows()}
    prompts_updated = 0
    prompt_rows = conn.execute(
        sa.text(
            "SELECT id, row_id, body FROM module_instance_data_rows "
            "WHERE table_slug = 'equipment_prompts' AND row_id = ANY(:ids)"
        ),
        {"ids": list(prompt_bodies)},
    ).fetchall()
    for row in prompt_rows:
        want = prompt_bodies.get(str(row[1]))
        if not want:
            continue
        body = row[2]
        if isinstance(body, str):
            body = json.loads(body)
        body = dict(body or {})
        if body.get("files_json") == want.get("files_json"):
            continue
        body["files_json"] = want.get("files_json")
        body["name"] = want.get("name") or body.get("name")
        body["path"] = want.get("path", body.get("path"))
        conn.execute(
            sa.text(
                "UPDATE module_instance_data_rows "
                "SET body = CAST(:body AS jsonb) WHERE id = :id"
            ),
            {"body": json.dumps(body, ensure_ascii=False), "id": row[0]},
        )
        prompts_updated += 1
    logger.info("wave13: equipment prompts refreshed: %s", prompts_updated)


def downgrade() -> None:
    pass
