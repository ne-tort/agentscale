# 04 — Search

Вход: `lineitems.json`. Выход: `offers.json`, `sources.log`.  
Схема оффера: [`schemas/offer.schema.json`](../schemas/offer.schema.json).  
Каскад: [`python tools/search_offers.py --run-id <id> --runs-dir runs`](../tools/search_offers.py).  
Характеристики / аналоги / сборки: [`07-equipment.md`](07-equipment.md).

Прайсы **глобальные**: `catalogs/db/` (имя файла = продавец), индекс `catalogs/index/*.sqlite`. **Локальная БД = seller_tier trusted автоматически.** Не копировать в проект.

Порядок по каждой позиции:

1. **Локальные БД** `catalogs/db/` — MCP `commerce-search`.
2. **S4B.ru** — MCP `commerce-s4b` (стратегия ниже).
3. **Веб** по `list_web_shops` — **не только если 1–2 пусты**. Иди в интернет для сверки P/N, характеристик, аналогов, наличия на DNS/Ozon/AliExpress и т.д. Список магазинов правит оператор: `/магазины`. Флаг `enabled=false` в allowlist — тогда веб для цен пропускай (уточнение характеристик всё равно можно с `kind=specs`).

## P/N vs описание (тот же принцип, что в БД и вебе)

- **Есть `part_number`** — сначала exact в catalogs (`search_by_part_number` / `equals_json`), затем S4B `[pn]`. Не размывай keywords, пока exact не исчерпан. Семейство той же фирмы — редко. Аналоги по attrs — только если пусто.
- **Нет P/N** — категория + constraints (бренд, RAM, порты…). Не ищи «первый Acer». Веб на уточнение линейки обязателен, если constraints дырявые.

## Локальные БД (MCP `commerce-search`)

Не проси «все строки». Сценарий: `list_databases` → `describe_database` → `distinct_values(category)` → узкий `query_database` (exact P/N или category+contains). LIMIT ≤100.

В `list_databases` есть виртуальная БД **`s4b-cache`** (если уже есть `catalogs/s4b/cache.sqlite`): это кэш live S4B, не локальный прайс. Exact P/N: `search_by_part_number` или `query_database('s4b-cache', equals_json='{"part_number":"..."}')`. Поля как у прайса: `part_number`, `title`, `price`, `supplier`, `availability` (`in_stock`/`on_order`), `lead_time`. `seller_tier` для этих строк — по trusted-list S4B, не «trusted автоматически».

## S4B (MCP `commerce-s4b`)

Креды: env. Кэш: `catalogs/s4b/cache.sqlite`, TTL `max_age_days` (default 3).

**Наличие:** только реальное `in_stock`. **«Под заказ» не использовать** — ни MCP, ни CLI:

- не ставь `include_on_order` (у MCP-тулов параметра нет; в CLI не передавай `--include-on-order`);
- не ищи в `s4b-cache` `availability=on_order`;
- не клади в `offers.json` / SQLite строки с `on_order`, listNoStock, сроком «под заказ N недель», qty=0.

S4B часто кладёт «под заказ» в **listStock** (колонка Тран) при Есть=1 — это не наличие. Фильтр тулов их отбрасывает; если такая строка всё же пришла — не брать.

**Доверенные продавцы S4B** — отдельно от локальных БД: `catalogs/s4b/trusted-sellers.json`, MCP `s4b_list_trusted_sellers`, бот **`/s4b доверенные`** (добавить/удалить). Это **сито**, не жёсткий запрет: если после фильтра пусто — повтори с `trusted_only=false` и покажи остальное как `seller_tier=unknown`.

### Стратегия запросов (по приоритету)

На **каждую** позицию — live API каждый прогон (~10 с cooldown между вызовами). После live смотри кэш с тем же фильтром.

| Тег | Когда | MCP |
|-----|--------|-----|
| `[pn]` | `lineitem.part_number` заполнен | `s4b_search_articles('["PN"]', trusted_only=true)` — P/N уходит в S4B как **sr=** (как keywords). Параметр `art=` — внутренние id S4B, для P/N не использовать. |
| `[pn-text]` | P/N только в title/description | тот же `s4b_search_articles` по извлечённым кандидатам |
| `[kw-strict]` | нет P/N: бренд + модель / constraints | `s4b_search_keywords` |
| `[kw-broad]` | `[kw-strict]` мусор или пусто | категория + 1–2 признака |
| `[batch-xlsx]` | оператор дал sr.xlsx | `s4b_parse_xlsx` → `s4b_search_xlsx` |

**Сужение / расширение:**

- После `[pn]` + кэш: релевантные exact у trusted → **стоп** по keywords, но **веб-сверку характеристик** всё равно сделай, если карточки ещё нет.
- Trusted пусто, upstream не пуст → `trusted_only=false`.
- Аналоги и «похожее семейство» — только по правилам 07-equipment, не вместо exact.

### Запись в offers.json

- `source_type`: `catalog` | `api` (s4b) | `web`.
- `seller_tier`: catalog → `trusted`; s4b trusted-list → `trusted`; веб-магазин из allowlist → `acceptable` если не сказано иное; иначе `unknown`.
- Несколько продавцов на позицию — **несколько офферов**.
- Не выдумывать цены/SKU. Пустой поиск → `search_gap`.

После идентификации модели: `equipment_upsert` в текущий проект.

## Веб (MCP `commerce-web-shops` + pipeline)

`list_web_shops` — allowlist хостов (`/магазины`, `enabled=false` → пропуск цен). Поиск и цены — **MCP spider**, не browser и не выдуманные цифры:

- **`web_offers_for_lineitem(lineitem)`** — на **каждую** позицию после catalog/S4B: каскад по enabled магазинам, items[] → `offers_add_variants` с `source_type: web`, `seller_tier: acceptable`.
- **`web_search_shop(shop_id, query)`** — точечный поиск в одном магазине (DNS: cookies `catalogs/web-cookies/dns.json` с qrator_*).
- **`web_cookie_status`** — `ready=false` → re-import (`tools/web_spider_cookies.py`).
- Уточнение без цен — `equipment_upsert` + `log_source(..., source_type=web_clarify)`.

Pipeline CLI: `search_offers --allow-web` вызывает тот же каскад (`tools/web_spider/cascade.py`) и дописывает в `offers.json`. S4B live — **`commerce-s4b` MCP**, не `search_offers --allow-api`.

## Rank (напоминание)

Primary: тот же P/N + trusted → мин. цена. Без P/N → лучшее совпадение, затем мин. цена. Alternatives сохранять. Непроверенный S4B — alternative или review.
