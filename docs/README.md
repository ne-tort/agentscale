# Prodavan — документация

**Current product / S1:** [PRODUCT.md](PRODUCT.md) — Pod UI, агенты с файлами в
project Pod. **Current plugin status:** [STOP-SHIP](migration/plugin-migration-status.md);
plugin v4 — target, а не подтверждённый vertical slice.

| Категория | Навигация | Назначение |
|---|---|---|
| Current product | [PRODUCT.md](PRODUCT.md), [employee-ui/](employee-ui/) | Текущий S1 и продуктовые требования |
| Current as-built | Код и манифесты; [migration/plugin-migration-status.md](migration/plugin-migration-status.md), [migration/plugin-acceptance-map.md](migration/plugin-acceptance-map.md) | Что есть и что действительно подтверждено; сейчас live acceptance отсутствует |
| Target design | [PLUGIN-AUTHORING.md](PLUGIN-AUTHORING.md), [ARCHITECTURE/plugin-architecture-complete.md](ARCHITECTURE/plugin-architecture-complete.md) с errata | Целевой plugin v4, не quickstart и не as-built |
| Accepted ADR | [architecture-migration/README.md](architecture-migration/README.md), [02-architecture/ADR-document-store-mongo.md](02-architecture/ADR-document-store-mongo.md), [02-architecture/ADR-search-index-opensearch.md](02-architecture/ADR-search-index-opensearch.md), [06-agent-runtime/adr-pod-agent-runtime.md](06-agent-runtime/adr-pod-agent-runtime.md) | Принятые решения и явные supersede; принятие ≠ реализация |
| Migration reports | [migration/](migration/), [plugin-cutover-safety.md](migration/plugin-cutover-safety.md) | Текущий STOP-SHIP, безопасные gates и датированная история |
| Ops | [07-infrastructure/](07-infrastructure/) | Актуальные k3s, Terraform, CI, Alembic и e2e runbooks |
| Historical snapshot | [target/12-layer-docs/](target/12-layer-docs/) | Устаревший снимок до перерасчёта, не текущая приёмка |
| History / legacy | [target/README.md](target/README.md), старые материалы 01…10 | Сохранённая история, не блокер разработки |

## Правила чтения

1. Каталог не определяет статус: актуальные Document Store, OpenSearch и
   agent-runtime ADR не становятся legacy из-за расположения в 01…10.
2. [LEGACY.md](LEGACY.md) — политика current / target / accepted ADR / as-built /
   migration reports / history. Исторические документы не удаляются.
3. Написанный код, тест или манифест не равен работающему сквозному сценарию.
   Acceptance [x] требует live evidence на конкретном HEAD.
4. [ARCHITECTURE/](ARCHITECTURE/) — датированные as-is-разборы и target design;
   [12-layer-docs](target/12-layer-docs/) больше не называется живым as-built.
5. Старые AI-канон и gap maps не расширяются без явной задачи. Новые продуктовые
   требования — в PRODUCT.md.