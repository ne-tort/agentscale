"""WAVE7 «Подбор товаров»: группы кандидатов (found_groups) + «Закупка» (procurement).

Форсированный сид модуля при деплое:
- upsert_product_modules обновляет meta-доки (tables/columns/views/actions)
  ВСЕХ инстансов mod_equipment — новый контракт метасинтаксиса применяется
  сразу после миграции (деплой → migrate.sh → alembic upgrade head);
- found_offers больше не пишется агентом: таблица становится материализацией
  автоматикой (equipment.pipeline); агент пишет found_groups
  (партномер + алиасы + match_kind exact|analog|doubt);
- новые таблицы found_groups / procurement, колонки trusted_sellers.delivery_rub
  и budget_lines.markup_source; view-контракт «Найденные товары» = группы;
- seed-zip MCP prodavan-equipment v2.0.0 перезаливается стартом API
  (SeedMcpBootstrapService), workspace обновляется при sync проекта.

Тестовые данные старой архитектуры (агентские found_offers без group_id)
не сохраняются: пайплайн их удаляет при первом прогоне.
"""

import sqlalchemy as sa

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100101"
down_revision = "2026093009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1) Форс-обновление meta модуля (все инстансы).
    upsert_product_modules(conn)

    # 2) Версия MCP-пакета в строках equipment_mcp → 2.0.0 (bootstrap при
    #    старте API перезальёт seed-zip и переприкрепит file_ref).
    conn.execute(
        sa.text(
            "UPDATE module_instance_data_rows "
            "SET body = jsonb_set(body, '{version}', '\"2.0.0\"'::jsonb) "
            "WHERE table_slug = 'equipment_mcp' AND row_id = 'equipment_mcp_default'"
        )
    )

    # 3) Бюджетные строки без источника маржи помечаем 'default' — пайплайн
    #    обновит маржу поставщика, но не тронет ручные правки (manual).
    conn.execute(
        sa.text(
            "UPDATE module_instance_data_rows "
            "SET body = jsonb_set(COALESCE(body, '{}'::jsonb), '{markup_source}', '\"default\"'::jsonb) "
            "WHERE table_slug = 'budget_lines' "
            "AND NOT (body ? 'markup_source')"
        )
    )


def downgrade() -> None:
    # Откат контракта метасинтаксиса не поддерживается (данные тестовые).
    pass
