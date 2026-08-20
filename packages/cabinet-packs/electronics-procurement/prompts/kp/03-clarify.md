# 03 — Clarify (веб на понимание)

Когда формулировка не даёт искать SKU: сленг, «16 портов», нет бренда, конфликт признаков, непонятный идентификатор в спеке.

Цель шага — **уточнить категорию, P/N (если есть) и constraints**, не купить товар. **Не экономь на вебе.**

1. Сформулируй 1–3 гипотезы категории.
2. **Веб:** `list_web_shops` — allowlist. Для уточнения характеристик: `web_search_shop` / `web_fetch_page` (MCP `commerce-web-shops`), `equipment_upsert`. Browser — только если spider вернул block/captcha. Несколько источников лучше одного.
3. Различай: описание vs явный партномер — см. [07-equipment.md](07-equipment.md).
4. Сохрани позиции: **`save_lineitems(run_id, items_json, phase='clarified')`** — обновляет `lineitems.json`, `needs-review.json` и SQLite. Либо точечно `offers_upsert_lineitem` + потом `save_lineitems` пакетом.
5. Карточка без цен: **`equipment_upsert`** (не `specs_upsert` — тот для фазы output).
6. Источник: **`log_source(run_id, url или заметка, source_type='web_clarify')`** → `sources.log`.
7. Запрещено: цена витрины на этом шаге как оффер. Офферы — фаза 04.
