"""WAVE11 фазы 2-3: каталог «Готовые сборки» + пайплайн каталога.

Новые таблицы (все `chats: all` — глобальная библиотека на проект, строки с
`session_id = NULL`, видны из любого чата):

- `build_groups` — домен совместимости (сокет / тип ОЗУ / форм-фактор)
  + бюджетный класс;
- `build_group_items` — пул оборудования группы: одна строка = одна
  альтернатива компонента, хранит КЛЮЧИ (партномер / алиасы / хэш) и
  `class_key` (`ram_16`, `ssd_256`). Класс не даёт сравнивать несравнимое;
- `ready_builds` — вариант конфигурации внутри группы + классовые атрибуты
  (ядра / ОЗУ / накопитель / GPU) для сопоставления с заявкой;
- `ready_build_slots` — слот: `type_id` + `qty` + `mode` (`dynamic` →
  дешевейший доступный компонент класса, может дрейфовать; `fixed` → закреплён).

Новое действие `ready_builds_sync` (`kind: equipment.ready_builds`): резолвит
ключи в лучшие цены одним батч-запросом в OpenSearch, разрешает dynamic/fixed
слоты, считает итоги сборок и агрегаты групп. Триггеры — записи в таблицы
каталога + кнопка «Обновить цены» + beat-расписание (час): цены меняются в
каталоге поставщика, а не в наших строках, поэтому одних триггеров мало.

Новые виды: `build_groups_list` / `build_group_settings` /
`build_group_items_list` / `build_group_item_settings` / `ready_builds_list` /
`ready_build_settings` / `ready_build_slots_list` / `ready_build_slot_settings`;
пункт хаба Управления «Готовые сборки».

Seeds only — данные не мигрируются (каталог наполняет ИИ через MCP).
"""

from alembic import op
from prodavan.application.platform.product_module_upsert import upsert_product_modules

revision = "2026100525"
down_revision = "2026100524"


def upgrade() -> None:
    conn = op.get_bind()
    upsert_product_modules(conn)


def downgrade() -> None:
    pass
