# WAVE14 — «Тендеры»: глобальная группировка позиций заказчика

Тендер — сущность уровня проекта (chats=all), группирующая позиции заказчика.
Состав тендера хранится в нём самом (`lines_json`); чат «берёт тендер в работу»
(действие `tender_attach`) — позиции копируются в `request_lines` чата со
связкой на тендер. Все метрики тендера (позиций, найдено %, сумма, маржа,
поставщики) материализует оборотный пайплайн по связанным позициям ВСЕХ чатов
проекта (та же «проектная» семантика, что у «Закупки»).

## Модель

### tenders (новая таблица, `chats=all`)

Редактируемые поля: `title` (required), `customer`, `deadline_at` (datetime —
срок подачи заявки), `status` (enum draft|active|submitted|won|lost|cancelled,
default draft), `comment`, `lines_json` (позиции: `[{id, title, part_number,
qty}]`, редактируются подтаблицей в карточке), `project_ids`.

Пишутся автоматикой (read_only): `positions_count` (max(план из lines_json,
факт связанных позиций) — до импорта виден плановый состав),
`linked_positions_count`, `found_pct` (доля связанных позиций со статусом
matched|selected), `sum_rub` (Σ qty × price_in по бюджетным снапшотам),
`margin_pct` (средневзвешенная наценка Σмаржа/Σвход), `sum_margin_rub`,
`suppliers_count` (уникальные seller бюджетных строк), `imported_chat_ids`
(кто взял в работу).

### request_lines: связь с тендером

Новые поля `tender_id` (row_id тендера текстом — позиция chat-scoped, тендер
chats=all, FK-ref не подходит; в списке позиций резолвится в название через
`lookup_table` на клиенте) и `tender_line_id` (id позиции в `lines_json` —
ключ идемпотентного импорта). Позиция привязана максимум к одному тендеру.

### «Взять в работу» (tender_attach)

Действие `equipment.tender_attach` (UI: кнопка «В чат» на строке/карточке
тендера; MCP: `tender_attach`). Каждая позиция `lines_json` upsert'ится в
`request_lines` АКТИВНОГО чата по ключу `tender_id + tender_line_id`:
повторный вызов обновляет title/part_number/qty существующих и добавляет
новые — дублей нет. Удалённые из тендера позиции в чате не трогаем (по ним
мог быть прогресс: группы/офферы/бюджет). После импорта — штатный прогон
пайплайна (материализация офферов → бюджет → закупка → агрегаты тендера).

### Агрегаты (_sync_tenders, конец EquipmentPipelineService.run)

Суммы/маржа считаются по `budget_lines` всех чатов проекта (через
`list_project_wide`), у которых `line_id` ∈ позиции с `tender_id` этого
тендера. Эффективная наценка строки — как в закупке: ручная (`markup_source
= manual`) из бюджета, иначе актуальная `margin_pct` поставщика из реестра
(`trusted_sellers`). Поставщики — уникальные `seller` бюджетных строк.

## UI

- Хаб «Управление → Подбор техники»: плитка «Тендеры» (иконка gavel), без
  требования активного чата.
- `tenders_list`: Название, Заказчик, Позиций, Найдено (percent), Сумма ₽
  (money), Маржа (margin_pair), Поставщиков, Срок подачи (deadline:
  «2 д. 12 ч.»; <1 дня — warning-цвет, просрочен — «Истёк» danger), Статус.
  `on_load: equipment_pipeline_sync` — свежие агрегаты при открытии.
  `row_tap` → форма, `primary_action: create_row` — «+ Добавить».
- `tenders_form`: стандартные поля + подтаблица позиций (виджет
  `tender_lines_table`) + read-only агрегаты + project_ids.
- `request_lines_list`: колонка «Тендер» (резолв `tender_id` → название
  тендера через обобщённый `lookup_table` в `formatModuleCell`).

Новые форматы ячеек (`module_cell_format.dart`): `money` (группировка
разрядов + «₽»), `percent`, `deadline` (относительный остаток). Warning/
danger-окраска дедлайна — в `collection_interpreter` тем же cellWidgets-пассом,
что «Нет цены»/«Под заказ».

## MCP (prodavan-equipment 3.1.0)

`tenders_list`, `tender_get`, `tender_upsert` (title/customer/deadline_at/
status/comment + `lines` — полная замена позиций), `tender_delete`,
`tender_positions_upsert` (merge по id; `replace=true` — полная замена),
`tender_attach` (взять в работу в текущий чат). Алиасы RU — в `mcp_aliases`.
Агрегатные поля пайплайн пересчитывает — агент их не пишет.

## Миграция

`2026101701_equipment_tenders` — только `upsert_product_modules` (метаданные:
таблица, колонки, представления, действия, MCP-алиасы; физических таблиц нет).
