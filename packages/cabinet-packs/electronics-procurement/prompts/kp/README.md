# Профиль kp — спека → поиск → КП

Порядок фаз, критерии и тулы для подбора товаров по спецификации. Другие задачи (тендеры и т.п.) — отдельные профили, не смешивать.

## Порядок

1. [`01-ingest.md`](01-ingest.md) — вход, `new_run`, `rows.json`
2. [`02-classify.md`](02-classify.md) — категории, P/N, `save_lineitems`
3. [`03-clarify.md`](03-clarify.md) — веб/уточнение грязных формулировок
4. [`04-search.md`](04-search.md) — каталог → S4B → витрины
5. [`05-rank.md`](05-rank.md) — primary vs alternatives
6. [`06-output.md`](06-output.md) — SQLite, MCP commerce-offers, `/кп` в боте
7. [`07-equipment.md`](07-equipment.md) — идентификация, характеристики, сборки..

## Критерии

- Цены, SKU, наличие — только из артефактов и тулов.
- Exact P/N, если он есть в спеке. Без P/N — по характеристикам.
- Trusted + тот же P/N → минимальная цена. Недоверенный дешевле — alternative.
- Только `in_stock`. «Под заказ» не использовать.

## Тулы

MCP в Telegram уже в сессии: `new_run`, `parse_spec`, `classify_rows`, `save_lineitems`, `commerce-search`, `commerce-s4b`, `offers_*`, `rank_offers`, `equipment_upsert`, `list_web_shops`.

CLI из корня проекта: `python ../../tools/… --runs-dir runs`.
