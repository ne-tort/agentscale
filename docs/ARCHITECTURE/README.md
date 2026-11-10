# Architecture audit

Датированный архитектурный аудит платформы: **as-is / проблемы / target-design**
по слоям. Это **не продуктовые требования**, не acceptance и не замена [PRODUCT.md](../PRODUCT.md),
кода, [текущего статуса миграции](../migration/plugin-migration-status.md) или
[ops-ранбука](../07-infrastructure/).

[target/12-layer-docs](../target/12-layer-docs/) — устаревший исторический
snapshot до перерасчёта, а не живая as-built карта.

## Как пользоваться

| Документ | Слой |
|----------|------|
| [layers/00-overview.md](layers/00-overview.md) | Карта слоёв, data-flow, сводка P0–P2 |
| [layers/01-meta-syntax-modules.md](layers/01-meta-syntax-modules.md) | Метасинтаксис, модули, materialize, SoT |
| [layers/02-pod-orchestration.md](layers/02-pod-orchestration.md) | Pod lifecycle, k8s, reconcile, workspace |
| [layers/03-claw-sdk-mcp.md](layers/03-claw-sdk-mcp.md) | Claw / SDK / MCP / токены / bridge |
| [layers/04-api-infra-access.md](layers/04-api-infra-access.md) | API, ACL, tenant infra, метрики |
| [layers/05-cross-cutting.md](layers/05-cross-cutting.md) | Kafka vs in-proc, durable ops, канон доков |

Шаблон каждого слоя: **Контекст → As-is → Проблемы (P0/P1/P2) → Target-design → Шаги рефакторинга**.

Приоритеты проблем:

- **P0** — ломает изоляцию, безопасность или корректность учёта; чинить первым.
- **P1** — системный костыль / нарушение заявленных принципов; блокирует масштабирование.
- **P2** — техдолг, UX/семантика, локальные дыры.

Реализация рефакторинга в этом наборе документов **не** делается — только анализ и целевой дизайн со ссылками на файлы.

## Связь с другими канонами

```text
PRODUCT.md              — current S1 + переходный strangler + target
architecture-migration   — принятые и предложенные plugin ADR
migration/               — STOP-SHIP, acceptance и безопасный cutover
12-layer-docs            — исторический snapshot до перерасчёта
ARCHITECTURE/layers      — датированный аудит и target design
07-infrastructure        — актуальные GitOps / k3s / CI runbooks
target/06-modules        — старый AI-канон метасинтаксиса (справка)
```
