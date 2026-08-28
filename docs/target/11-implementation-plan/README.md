# 11 — Implementation plan (инфраструктура реализации)

План **послойной** реализации Prodavan по канону [`docs/target/`](../README.md).  
Не backlog с оценками — **контракты + критерии готовности**, по которым ведут чеклисты и не закрывают слой «наполовину».

Код сейчас = [STUB](../../../STUB.md). Legacy = [LEGACY](../../LEGACY.md) (не следовать).

## Как пользоваться

1. Читать [00-rules.md](00-rules.md) — анти-«готово», изоляция, контракты.
2. Смотреть [sequence.md](sequence.md) — порядок и параллельные треки.
3. На слой — файл `LNN-*.md`: контракты, DoD, ссылки на канон, чеклист.
4. Сводка статусов — [checklist-master.md](checklist-master.md).
5. Реестр контрактов между слоями — [contracts-index.md](contracts-index.md).
6. **As-built** — [`12-layer-docs/`](../12-layer-docs/) (семантика / что-как / **Quality 0–10**); без карточки и без **Quality ≥ 8** слой нельзя считать `done`.

**Правило закрытия слоя:** все пункты DoD = `done`, veto пуст, as-built актуален, **Quality ≥ 8** ([quality-score](../12-layer-docs/quality-score.md)). Минимальный прототип ≠ готовность.

## Карта слоёв

| ID | Слой | Изоляция | Канон |
|----|------|----------|-------|
| [L00](L00-platform-skeleton.md) | Platform skeleton (API/DB/Flutter layout) | Высокая | STUB, alembic, apps layout |
| [L01](L01-identity.md) | Identity & entitlements | Средняя (нужен KC) | [10](../10-identity-keycloak/) |
| [L02](L02-ui-core.md) | UI mobile core | **Полная** (без backend) | [07](../07-ui-mobile-core/) |
| [L03](L03-ai-keys.md) | AI Provider Keys | Высокая (Company id как контракт) | [02](../02-ai-provider-keys/) |
| [L04](L04-admin-company.md) | Admin + Company control plane | Средняя | [01](../01-platform-admin/), [03](../03-companies/) |
| [L05](L05-employee-shell.md) | Employee shell + cabinet entry | Средняя | [04](../04-employees/), [session](../10-identity-keycloak/session.md) |
| [L06](L06-cabinet-runtime.md) | Cabinet Runtime (meta/data/MCP/bundles) | Высокая (без agent) | [05](../05-cabinets/) |
| [L07](L07-projects-runtime.md) | Projects, materialize, container | Средняя | [06](../06-projects-runtime/) |
| [L08](L08-agent-providers.md) | AgentProviderPort + adapters | Высокая (fake cwd) | [08](../08-agent-providers/) |
| [L09](L09-vertical-integration.md) | Vertical wire-up (triggers, chat, metrics) | Низкая | gap-map + L01…L08 |
| [P1](P1-pod-service.md) | pod_service BC (Pod runtime, 1:1, stub) | Высокая | [14](../14-project-containers/) — **done** |
| [P2](P2-k3s-runtime.md) | k3s real adapter (lifecycle, hydrate, metrics) | Высокая | [14/k3s-runtime](../14-project-containers/k3s-runtime/) после P1 |

## С чего начинаем (кратко)

1. **Параллельно:** L00 + L02 (скелет и полный UI-core без API).  
2. Затем **L01** (Identity) — без него нельзя честно закрыть control plane.  
3. **Параллельно после L00:** L03 (Keys) и каркас L06 Runtime (schema/meta API без UI).  
4. L04 / L05 цепляются к контрактам L01–L03 и L02.  
5. L07 после стабильного materialize-hook L06.  
6. L08 можно прототипировать на fake workspace **до** L07, но **закрыть** — только с L03+L07.  
7. L09 — только когда контракты L01…L08 зелёные.

Детали: [sequence.md](sequence.md).

## Связь с gap-map

Волны в [09-gap-map.md](../09-gap-map.md) — обзор. Этот каталог — **операционный** план с DoD. При расхождении приоритет у канона модулей `01`…`10`; план уточняет порядок, не подменяет продукт.
