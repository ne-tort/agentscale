"""WAVE12: «Готовые сборки» v2 — карточки комплектующих + матрица совместимости.

Старая модель WAVE11 (группы совместимости ``build_groups`` + пул
``build_group_items`` + бюджетные классы ``budget_tier``/``class_key``/
``class_signature``) убирается совсем. Новая модель:

- ``equipment_types.fields_json`` — шаблон характеристик типа: kind
  (text|enum|groups), required, compat_domain (socket/ram_type/form_factor),
  values/groups — заранее заданные значения, не свободные строки;
- ``part_cards`` — карточки комплектующих: ключи (партномер/алиасы/хэш) +
  type_id + attrs (значения из групп типа);
- ``ready_builds``/``ready_build_slots`` — слот = тип + constraints
  (поле → значение группы) + qty + опциональная закреплённая card_id;
- ``equipment_builds`` — глобальная ССЫЛКА на готовую сборку (ready_build_id),
  состав зеркалится пайплайном чата, копирования больше нет.

Данные старой модели не переносятся (семантика несовместима): строки
build_groups / build_group_items / ready_builds / ready_build_slots /
equipment_items / equipment_builds удаляются, каскадом — их found_groups
(owner_kind='build'), офферы этих групп и бюджетные строки сборок
(бюджет пересоберётся пайплайном).

Сид-строки ``equipment_types`` (created_by='module_seed') получают новый
fields_json с группами характеристик — seed-rows сами не обновляются
(ON CONFLICT DO NOTHING), поэтому правим явно. Системные промпты
(AGENTS.md + rules) переписаны под новые инструменты — старые ссылки на
build_group_*/class_key сбивали бы агента, поэтому обе дефолтные строки
обновляются принудительно.

Идемпотентно: повторный прогон ничего не меняет.
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

revision = "2026101401"
down_revision = "2026100534"

_DROP_TABLES = ("build_groups", "build_group_items", "equipment_items")


def _delete_rows(conn, table_slug: str, extra_where: str = "") -> int:
    sql = "DELETE FROM module_instance_data_rows WHERE table_slug = :t"
    if extra_where:
        sql += f" AND {extra_where}"
    result = conn.execute(sa.text(sql), {"t": table_slug})
    return int(result.rowcount or 0)


def upgrade() -> None:
    conn = op.get_bind()

    # ---------------------------------------------------------- чистка данных
    # 1) таблицы старой модели + строки каталога сборок (семантика несовместима)
    dropped: dict[str, int] = {}
    for slug in _DROP_TABLES + ("ready_builds", "ready_build_slots", "equipment_builds"):
        dropped[slug] = _delete_rows(conn, slug)

    # 2) группы-кандидаты слотов сборок (owner_kind='build') + их офферы
    build_groups_ids = [
        str(r[0])
        for r in conn.execute(
            sa.text(
                "SELECT row_id FROM module_instance_data_rows "
                "WHERE table_slug = 'found_groups' "
                "AND body->>'owner_kind' = 'build'"
            )
        ).fetchall()
    ]
    offers_deleted = 0
    groups_deleted = 0
    if build_groups_ids:
        offers_deleted = int(
            conn.execute(
                sa.text(
                    "DELETE FROM module_instance_data_rows "
                    "WHERE table_slug = 'found_offers' "
                    "AND body->>'group_id' IN :ids"
                ).bindparams(sa.bindparam("ids", expanding=True)),
                {"ids": build_groups_ids},
            ).rowcount
            or 0
        )
        groups_deleted = _delete_rows(
            conn, "found_groups", "body->>'owner_kind' = 'build'"
        )

    # 3) бюджетные строки сборок — пересоберутся пайплайном
    budget_deleted = _delete_rows(
        conn, "budget_lines", "COALESCE(body->>'build_id', '') <> ''"
    )
    logger.info(
        "wave12 cleanup: %s, build_groups=%s offers=%s budget=%s",
        dropped,
        groups_deleted,
        offers_deleted,
        budget_deleted,
    )

    # ---------------------------------------------------------- мета модуля
    upsert_product_modules(conn)

    # ---------------------------------------------------------- типы-шаблоны
    # seed-строки equipment_types получают новый fields_json (группы/домены);
    # пользовательские типы (created_by != module_seed) не трогаем — их
    # старые поля читаются как kind=text.
    type_bodies = {
        row["row_id"]: row["body"] for row in _equipment_type_seed_rows()
    }
    types_updated = 0
    seed_rows = conn.execute(
        sa.text(
            "SELECT id, row_id, body FROM module_instance_data_rows "
            "WHERE table_slug = 'equipment_types' "
            "AND created_by = 'module_seed' "
            "AND row_id = ANY(:ids)"
        ),
        {"ids": list(type_bodies)},
    ).fetchall()
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
        types_updated += 1
    logger.info("wave12: equipment_types templates refreshed: %s", types_updated)

    # ---------------------------------------------------------- промпты
    # Инструменты переименованы/переосмыслены (part_cards_*, ready_builds_*),
    # старые промпты с build_group_*/class_key сбивали бы агента — обе
    # системные строки обновляются принудительно.
    prompt_bodies = {row["row_id"]: row["body"] for row in _equipment_prompt_seed_rows()}
    prompts_updated = 0
    prompt_rows = conn.execute(
        sa.text(
            "SELECT id, row_id, body FROM module_instance_data_rows "
            "WHERE table_slug = 'equipment_prompts' "
            "AND row_id = ANY(:ids)"
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
    logger.info("wave12: equipment prompts refreshed: %s", prompts_updated)


def downgrade() -> None:
    pass
