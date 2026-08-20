# 05 — Rank & select

Вход: `offers.json`. Tier: catalog/db и S4B trusted-list → `trusted`; иначе `unknown`.  
Выход: `selection.json`. Тул: `python tools/rank_offers.py --run-id <id> --runs-dir runs [--project <имя>]`.  
С `--project` после rank варианты попадают в `commerce.sqlite` (best = primary). Дальше MCP `commerce-offers` — оценки и правки. КП xlsx не писать.

## Политика цены

### Тот же партномер (в спеке есть P/N)

Среди **доверенных** продавцов (`seller_tier=trusted`: локальная БД или S4B trusted-list) **всегда выигрывает самая низкая известная цена**. Кто «более свой» из trusted — не важно.

Недоверенный с более низкой ценой **не** бьёт доверенного: он в `alternatives` / review, не в primary без оператора.

Exact этот P/N (~90%). Семейство той же фирмы — только если exact нет. Аналоги по характеристикам — только если нет ни exact, ни семейства ([07-equipment.md](07-equipment.md)).

### Нет P/N (название / фирма / характеристики)

Ищется **лучшее совпадение**, при равном (или близком) совпадении — **самая низкая цена**.

Порядок: `match_type` (exact → equivalent → alternative) → `relevance` → цена → tier как тай-брейк. Не выбирать дорогой «чуть точнее» вместо явно лучшего дешёвого совпадения той же ступени match. Не брать «любую мышь».

`avoid` не в primary. Нет usable офферов → `needs-review`. **`on_order` / «под заказ» не брать в primary и не оставлять как выбранный вариант** — это не наличие.

## Что писать в selection

1. **primary** — один оффер по правилам выше.
2. **alternatives** — все остальные релевантные (другие продавцы того же SKU, хорошие замены). Не удалять из `offers.json`; после `--project` / `offers_import_run` они в SQLite.
3. `role` в таблице: `primary` | `alternative`. `selected` true только у primary.
