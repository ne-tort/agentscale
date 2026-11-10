# 12 — Layer docs (HISTORICAL SNAPSHOT)

> **УСТАРЕВШИЙ SNAPSHOT ДО ПЕРЕРАСЧЁТА.** Этот каталог больше не является живой
> as-built картой или текущей приёмкой. Статусы, quality scores, ссылки на Flutter
> и утверждения о слоях относятся к прошлому срезу и **не пересчитаны** после
> React/migration/plugin-аудитов.
>
> **Актуальные ориентиры:**
> - [PRODUCT.md](../../PRODUCT.md) — current S1 / strangler / target;
> - [plugin-migration-status.md](../../migration/plugin-migration-status.md) —
>   независимый STOP-SHIP на HEAD `6ee720dd`;
> - [plugin-acceptance-map.md](../../migration/plugin-acceptance-map.md) — только
>   live evidence, сейчас без подтверждённых пунктов;
> - [LEGACY.md](../../LEGACY.md) — категории документации.
>
> Код и текущие манифесты важнее любой карточки ниже. Написанный код также не
> равен работающему live сценарию.

## Как пользоваться сохранённым snapshot

1. Читать карточки как **историю реализации и навигацию по старым ссылкам**, а не
   как список текущих возможностей.
2. Перед использованием конкретного утверждения сверить его с текущим кодом и
   актуальным accepted ADR; при расхождении верить коду и свежим evidence.
3. Не использовать `done` или `Quality ≥ 8` как разрешение на merge, cutover,
   включение plugin platform или удаление migration-only helpers.
4. Полный corpus не переписывается этой правкой. Перерасчёт каждой карточки —
   отдельная задача с датой, HEAD и явными evidence.
5. [00-principles.md](00-principles.md), [quality-score.md](quality-score.md) и
   [_TEMPLATE.md](_TEMPLATE.md) описывают **старую** методику ведения карточек,
   а не действующий процесс acceptance.
6. [map.md](map.md) и [tenant-infra-gateway.md](tenant-infra-gateway.md) —
   исторические as-is references; `:8001` остаётся agent-only и не является
   Plugin Gateway.

## Сохранённые исторические карточки

| ID | Слой | Исторический Status | Исторический Quality |
|----|------|---------------------|----------------------|
| [L00](L00-platform-skeleton.md) | Platform skeleton | done | 8 |
| [L01](L01-identity.md) | Identity & entitlements | partial | 7 |
| [L02](L02-ui-core.md) | UI mobile core | done | 8 |
| [L03](L03-ai-keys.md) | AI Provider Keys | done | 8 |
| [L04](L04-admin-company.md) | Admin + Company | doing | 7 |
| [L05](L05-employee-shell.md) | Employee shell | not_started | 0 |
| [L06](L06-cabinet-runtime.md) | Cabinet Runtime | done | 8 |
| [L07](L07-projects-runtime.md) | Projects & container | not_started | 0 |
| [L08](L08-agent-providers.md) | Agent providers | not_started | 0 |
| [L09](L09-vertical-integration.md) | Vertical integration | not_started | 0 |

**При любом расхождении НЕ верить карточке автоматически.** До отдельного
перерасчёта всё содержимое каталога — history, а не current as-built.