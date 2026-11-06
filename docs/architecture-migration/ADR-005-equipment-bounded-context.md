# ADR-005: Equipment bounded context

- **Статус:** Принято
- **Дата:** 2026-11-01

## Контекст

Подбор техники объединяет ingestion каталогов, требования заказчика, matching, офферы, закупку и экспорт. Если агент или crawler сможет писать производные офферы и цены напрямую, нарушаются provenance, stale policy и человеческое подтверждение.

## Решение

Equipment разделяется на bounded contexts с явным владением:

1. **Catalog ingestion** владеет источниками, запусками, raw artifacts и normalized offer snapshots.
2. **Requirement/matching** владеет request lines, candidate groups и объяснением решения агента.
3. **Offer/selection** строит materialized offers из актуальных snapshots и владеет stale/manual selection.
4. **Commercial calculation** владеет budget lines, supplier margin/delivery, procurement и exports.
5. **B2B intake** владеет внешними тендерами и их provenance, но не ценами.
6. **Agent workflow** владеет MCP intents, progress и audit, но не доменными фактами.

Агент может писать request lines и candidate groups через typed gateway. Crawler пишет только immutable normalized snapshots. Materialization pipeline вычисляет offers/best/rank; selection и коммерческие изменения требуют platform или human action. OpenSearch остаётся производной поисковой проекцией, а не source of truth.

## Последствия

- Источник цены, свежесть и provenance сохраняются независимо от UI и модели.
- Производные таблицы можно пересобирать идемпотентно из snapshots и решений.
- MCP-поверхность должна быть разделена по intent/read/query и защищена серверными ACL.
- На первом этапе сущности могут оставаться JSONB module rows, если сохраняются стабильные IDs, ссылки, scope и version semantics.
