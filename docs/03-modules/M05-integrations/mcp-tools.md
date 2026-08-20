# M05 — MCP tools (интеграции)

Tools модуля M05 экспонируются через MCP-сервер **`prodavan-integrations`** (регистрация — M06). Namespace: `integrations.*` и делегирование в `catalog.*` / `s4b.*`.

## Сервер

| id | transport | scope |
| --- | --- | --- |
| `prodavan-integrations` | stdio / SSE | cabinet-scoped via `CABINET_ID` env |

Контекст сессии агента передаёт `cabinet_id` из JWT claims (M07 → M06).

## Tools

### `integrations.get_policy`

Текущая effective policy кабинета.

**Args:** нет (cabinet из контекста)

**Returns:** JSON `CabinetIntegrationPolicy`

### `integrations.list_web_shops`

Allowlist + метаданные адаптеров. Эквивалент Commerce `list_web_shops`, но **отфильтрованный**.

**Returns:**

```json
{
  "shops": [
    { "shop_id": "dns", "name": "DNS", "enabled": true, "rate_limit_rpm": 30 }
  ]
}
```

### `integrations.web_search`

Поиск в разрешённом магазине.

| arg | type | required |
| --- | --- | --- |
| `shop_id` | string | да |
| `query` | string | да |
| `part_number` | string | нет |
| `limit` | int | default 15 |

**Errors:** `INTEGRATION_NOT_ALLOWED`, `RATE_LIMIT_EXCEEDED`

### `s4b.status`

Статус API и credentials кабинета.

### `s4b.ping`

Проверка связи.

### `s4b.list_trusted_sellers`

Список trusted sellers кабинета.

### `s4b.search_articles`

Поиск по артикулам/P/N.

| arg | type | notes |
| --- | --- | --- |
| `articles` | string[] | до 50 |
| `trusted_only` | bool | default из policy |
| `in_stock_only` | bool | **always true** (не параметр агента) |

### `s4b.search_keywords`

Поиск по ключевым словам (без P/N).

### `s4b.cache_search`

Поиск в локальном s4b-cache кабинета.

### `s4b.cache_purge`

Очистка кэша (`max_age_days`).

### `s4b.set_max_age_days`

TTL политики кэша.

## Маппинг Commerce → Prodavan

| Commerce (legacy) | Prodavan M05 |
| --- | --- |
| `commerce-search.list_web_shops` | `integrations.list_web_shops` |
| `commerce-search.search_catalog_text` | `catalog.search_text` (M04) |
| `commerce-s4b.s4b_*` | `s4b.*` |
| `include_on_order` | **удалён** — не существует |
| global `catalogs/` | `tenants/.../catalogs/` per cabinet |

## Profile tool filter (M06)

Профиль `kp` включает:

```yaml
tools:
  - integrations.list_web_shops
  - integrations.web_search
  - s4b.status
  - s4b.search_articles
  - s4b.search_keywords
  - s4b.list_trusted_sellers
  - s4b.cache_search
```

Профиль `readonly-audit` — только `integrations.get_policy`, `s4b.status`.

## Rate limit в MCP

Перед каждым внешним вызовом:

1. `internal/check-rate-limit`
2. при `allowed=false` → tool error с `retry_after_seconds`

Метрики: см. M09 `prodavan_integration_rate_limit_hits_total`.

## Контракт Offer

Tools возвращают `RawOffer[]`:

```json
{
  "seller": "dns",
  "part_number": "ABC123",
  "title": "...",
  "price_rub": 1500.00,
  "in_stock": true,
  "url": "https://...",
  "source_ref": "integrations:web:dns:...",
  "trusted_seller": false,
  "fetched_at": "2026-08-20T10:00:00Z"
}
```

Агент **не** должен видеть сырой HTML или API tokens в result.
