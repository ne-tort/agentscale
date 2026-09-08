# 12 — Layer docs (as-built)

**Живая** модульная документация реализации: сжатая картина каждого слоя — семантика, что сделано, как сделано, контракты, связи, **оценка качества 0–10**.

| Документ | Роль |
|----------|------|
| Канон `01`…`10` | **Что должно быть** (продукт) |
| План `11` | **Как закрывать** слой (DoD, порядок) |
| **Этот каталог `12`** | **Что уже есть в коде** и как это устроено |

Цель: при следующей задаче **сначала** читать карточку слоя здесь, а не восстанавливать логику по всему репозиторию.

## Как пользоваться

1. [00-principles.md](00-principles.md) — обязательные разделы и правила обновления.
2. [quality-score.md](quality-score.md) — шкала **0–10**, рубрика, потолки (`done` ⟹ ≥ 8).
3. [_TEMPLATE.md](_TEMPLATE.md) — каркас новой/пустой карточки.
4. `LNN-*.md` — карточка слоя (одна на L00…L09).
5. [map.md](map.md) — сводная картина связей **по факту реализации**.
6. План DoD: [11-implementation-plan](../11-implementation-plan/).
7. [tenant-infra-gateway.md](tenant-infra-gateway.md) — **design** (not as-built): Pod → API access to Redis/Kafka/DB/Content.

**Агентам / разработчикам:** изменение поведения слоя без обновления его `LNN-*.md` (включая `Quality`) в том же PR — дефект процесса.

## Карточки

| ID | Слой | Status | Quality |
|----|------|--------|---------|
| [L00](L00-platform-skeleton.md) | Platform skeleton | done | **8** |
| [L01](L01-identity.md) | Identity & entitlements | partial | **7** |
| [L02](L02-ui-core.md) | UI mobile core | done | **8** |
| [L03](L03-ai-keys.md) | AI Provider Keys | done | **8** |
| [L04](L04-admin-company.md) | Admin + Company | doing | **7** |
| [L05](L05-employee-shell.md) | Employee shell | not_started | **0** |
| [L06](L06-cabinet-runtime.md) | Cabinet Runtime | done | **8** |
| [L07](L07-projects-runtime.md) | Projects & container | not_started | **0** |
| [L08](L08-agent-providers.md) | Agent providers | not_started | **0** |
| [L09](L09-vertical-integration.md) | Vertical integration | not_started | **0** |

При расхождении с карточкой верить карточке. Шкала: [quality-score.md](quality-score.md).
