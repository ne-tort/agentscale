# Checklist master — готовность реализации

Статусы: `todo` | `doing` | `blocked` | `done`.  
Слой `done` только если DoD файла слоя выполнен, veto-list пуст ([00-rules](00-rules.md)), as-built карточка [`12-layer-docs/LNN-*.md`](../12-layer-docs/) = `done` с заполненными § Что/Как сделано, и **Quality ≥ 8** ([quality-score](../12-layer-docs/quality-score.md)).

Обновлять этот файл при закрытии слоя (PR реализации).

| Слой | Статус | Quality | As-built | Доказательство (PR / suite) | Блокеры |
|------|--------|---------|----------|----------------------------|---------|
| [L00](L00-platform-skeleton.md) | done | 8 | [12](../12-layer-docs/L00-platform-skeleton.md) | L00 skeleton commit | |
| [L01](L01-identity.md) | todo | 0 | [12](../12-layer-docs/L01-identity.md) | | |
| [L02](L02-ui-core.md) | done | 8 | [12](../12-layer-docs/L02-ui-core.md) | L02 UI core commit | |
| [L03](L03-ai-keys.md) | todo | 0 | [12](../12-layer-docs/L03-ai-keys.md) | | |
| [L04](L04-admin-company.md) | todo | 0 | [12](../12-layer-docs/L04-admin-company.md) | | |
| [L05](L05-employee-shell.md) | todo | 0 | [12](../12-layer-docs/L05-employee-shell.md) | | |
| [L06](L06-cabinet-runtime.md) | todo | 0 | [12](../12-layer-docs/L06-cabinet-runtime.md) | | |
| [L07](L07-projects-runtime.md) | todo | 0 | [12](../12-layer-docs/L07-projects-runtime.md) | | |
| [L08](L08-agent-providers.md) | todo | 0 | [12](../12-layer-docs/L08-agent-providers.md) | | |
| [L09](L09-vertical-integration.md) | todo | 0 | [12](../12-layer-docs/L09-vertical-integration.md) | | |

## Фазы (сводка)

| Фаза | Условие выхода | Статус |
|------|----------------|--------|
| A фундамент (L00+L01+L02+L03 + API L06) | контракты C-* поставщиков зелёные | doing (L00 done) |
| B control (L04+L05) | UX contracts 01/03/04 | todo |
| C execution (L07+L08) | materialize + AgentEvent | todo |
| D vertical (L09) | E2E + metrics | todo |

## Быстрый veto (глобальный)

Перед любым «мы готовы к прод» проверить:

- [ ] Нет password-login канона в API
- [ ] Нет cabinets в JWT
- [ ] Нет static `profile_id` modules как продукт
- [ ] Нет raw SQL MCP
- [ ] Нет `cli_subscription` → agent
- [ ] Нет GLM / OpenClaw
- [ ] Нет модалок выбора сущностей
- [ ] Peer schema isolation держится под тестами

## Как вести чеклист внутри слоя

В каждом `LNN-*.md` секция DoD — копировать в issue/PR checklist 1:1.  
Не заводить параллельный «урезанный» список готовности.
