# Architecture audit

Архитектурный аудит платформы Prodavan: **as-is / проблемы / target-design** по слоям.

Это **не** продуктовые требования ([PRODUCT.md](../PRODUCT.md)), **не** as-built карточки ([target/12-layer-docs](../target/12-layer-docs/)) и **не** ops-ранбук ([07-infrastructure](../07-infrastructure/)). Здесь — разбор абстракций, изоляции, ответственности и целевого дизайна после MVP.

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
PRODUCT.md          — что строим (продукт)
12-layer-docs       — что уже в коде (as-built карточки)
ARCHITECTURE/layers — как устроено и куда расти (этот аудит)
07-infrastructure   — GitOps / k3s / CI
target/06-modules   — legacy AI-канон метасинтаксиса (справка)
```
