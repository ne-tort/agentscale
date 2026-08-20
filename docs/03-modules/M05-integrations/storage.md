# M05 — Хранилища файлов и кэши

M05 использует **три слоя хранения**: PostgreSQL (политики), Redis (rate limits + hot cache), object storage (S4B cache, опционально).

## Layout per cabinet

```text
tenants/{tenant_id}/cabinets/{cabinet_id}/
  integrations/
    s4b-cache/           # API-кэш S4B (не trusted-прайс)
      YYYY-MM-DD/
        {hash}.json
    web-snapshots/       # опционально: HTML/JSON снимки для отладки
      {shop_id}/{run_id}/
```

**Важно:** путь включает `tenant_id` и `cabinet_id`. Нет общего `catalogs/` на уровне платформы для боевых данных кабинета.

## S4B cache

### Назначение

Кэш ответов S4B API снижает rate limit и latency. Это **API-кэш**, не локальный trusted-прайс.

### Формат файла

```json
{
  "cabinet_id": "uuid",
  "query": { "type": "articles", "part_number": "ABC123" },
  "fetched_at": "2026-08-20T10:00:00Z",
  "max_age_days": 7,
  "items": []
}
```

### TTL

| тип | default TTL |
| --- | --- |
| search_articles | 24h |
| search_keywords | 12h |
| price snapshot | 6h |

Политика `s4b_max_age_days` per cabinet (default 7) — через MCP tool `s4b.set_max_age_days`.

### Purge

- Cron: удаление файлов старше `max_age_days`
- Manual: API `POST .../s4b/cache/purge` → MCP `s4b.cache_purge`

## Web shop snapshots

Опционально при `DEBUG_INTEGRATIONS=true`:

```text
web-snapshots/{shop_id}/{iso_timestamp}.html
```

Production: **выключено** по умолчанию (PII, объём).

## Локальные прайсы (trusted)

Загрузка прайсов кабинета — модуль M04 (catalog import). M05 только ссылается на mount point:

```text
tenants/{tenant_id}/cabinets/{cabinet_id}/catalogs/db/{name}.sqlite
```

MCP `catalog.list_databases` читает только каталоги **текущего кабинета**.

## Redis keys (non-persistent)

| key | TTL | содержание |
| --- | --- | --- |
| `policy:{cabinet_id}` | 60s | JSON effective policy |
| `s4b:trusted:{cabinet_id}` | 300s | set seller_ids |
| `rl:*` | window | counters |

## Quotas

| ресурс | лимит default | настраивается |
| --- | --- | --- |
| s4b-cache disk per cabinet | 500 MB | tenant plan |
| web-snapshots | 0 (off) | feature flag |
| trusted catalog sqlite files | 50 files | admin |

При превышении quota: `507 STORAGE_QUOTA_EXCEEDED`, purge oldest cache first.

## Backup

- PostgreSQL: включено в tenant backup (M08)
- s4b-cache: **не бэкапится** — восстанавливается из API
- Redis: ephemeral

## Безопасность путей

- Все операции через `StorageService` с проверкой prefix `tenants/{tenant_id}/cabinets/{cabinet_id}/`
- Path traversal (`../`) → `400 INVALID_PATH`
- SSE-KMS encryption at rest для object storage
