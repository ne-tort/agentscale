# 06 — Варианты в SQLite и КП

Источник правды: `projects/<имя>/commerce.sqlite` (MCP **`commerce-offers`**).  
xlsx **не пишет агент**. Оператор в Telegram: `/позиции`, `/варианты`, `/кп`.

Шаблон файла (когда оператор просит КП): `../../templates/kp-template.xlsx`.

## Что делает ИИ

1. После search/rank — **все релевантные офферы** по позиции в SQLite, не только победитель.
2. Оценки: `relevance` (соответствие), `ai_confidence` (уверенность), цена как есть из источника. Шкала **0–10** или **0–1** (1 = 10/10).
3. Пометить **один** `is_best` на позицию (`offers_set_best`).
4. Характеристики на фазе output — `specs_upsert` / `specs_link` если карточка ещё не создана через `equipment_upsert`. Одна таблица `specs` в SQLite: identify/clarify/search → **equipment_upsert**; связь с вариантами при выдаче → **specs_upsert** / **specs_link**.
5. В чат: run id, сколько позиций / с best / на ревью. Написать, что файл — команда **`/кп`**.

Не выдумывать цену, SKU, P/N.

## MCP уже в сессии

В Telegram инструменты `commerce-*` **уже подключены** как обычные тулы (`list_databases`, `offers_import_run`, …). Не вызывай `mcp_auth` / `GetMcpTools`. Не пиши «MCP недоступен». CLI — только после ошибки конкретного тула.

## MCP `commerce-offers`

| Tool | Зачем |
| --- | --- |
| `offers_upsert_lineitem` | позиция спеки |
| `offers_add_variants` | пачка офферов (`lineitem_id` обязателен) |
| `offers_import_run` | после rank: `lineitems.json` + `offers.json` + `selection.json` |
| `offers_list_lineitems` / `offers_list_best` | посмотреть глазами ИИ |
| `offers_list_variants` | пагинация, `alts_only` |
| `offers_score` | поправить оценки |
| `offers_set_best` / `offers_delete` | выбор / убрать мусор |
| `offers_get` | один вариант по целому `n` |
| `specs_upsert` / `specs_for_variant` / `specs_link` | характеристики |

`project` = имя из `[Проект: …]`. `runs_dir` в проекте = `runs`.

После `rank_offers.py --project <имя>` sqlite уже заполнен — дальше только оценки и best, если нужно поправить.

## Не делать

- MCP `commerce-fill` / `kp_fill_*` / писать строки в xlsx.
- `claw-attach-file …/kp.xlsx` как обязательный шаг фазы.
- Помечать прогон `final` без «ок» оператора.

## Оператор в боте

- `/позиции` — лучшие по строкам спеки, кнопки альтернатив.
- `/варианты <n> [стр]` — пагинация, «лучший» / удалить / характеристики.
- `/кп` — бот собирает xlsx (релевантность ≥ 6/10 + текущий best) и шлёт документ.

В xlsx: выпадающий **партномер** (группы), затем **продавец**; наименование и цена — формулы. Макросы не нужны.
