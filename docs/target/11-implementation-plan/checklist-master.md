# Checklist master — готовность реализации

Статусы: `todo` | `doing` | `blocked` | `done`.  
Слой `done` только если DoD файла слоя выполнен, veto-list пуст ([00-rules](00-rules.md)), as-built карточка [`12-layer-docs/LNN-*.md`](../12-layer-docs/) = `done` с заполненными § Что/Как сделано, и **Quality ≥ 8** ([quality-score](../12-layer-docs/quality-score.md)).

Обновлять этот файл при закрытии слоя (PR реализации).

| Слой | Статус | Quality | As-built | Доказательство (PR / suite) | Блокеры |
|------|--------|---------|----------|----------------------------|---------|
| [P0 platform-infra](P0-platform-infra.md) | doing | n/a | [13](../13-platform-infra/) + [L00](../12-layer-docs/L00-platform-skeleton.md) | locks/rate_limit; celery ready; list_prefix; default-deny | Kafka sole-path; Helm/TLS; live MinIO mount |
| [L00](L00-platform-skeleton.md) | done | 8 | [12](../12-layer-docs/L00-platform-skeleton.md) | L00 skeleton commit | |
| [L01](L01-identity.md) | partial | 7 | [12](../12-layer-docs/L01-identity.md) | identity + JWT tests | live KC Admin/realm |
| [L02](L02-ui-core.md) | done | 8 | [12](../12-layer-docs/L02-ui-core.md) | L02 UI core commit | |
| [L03](L03-ai-keys.md) | done | 8 | [12](../12-layer-docs/L03-ai-keys.md) | ai-keys CRUD+resolve | Vault backend |
| [L04](L04-admin-company.md) | doing | 7 | [12](../12-layer-docs/L04-admin-company.md) | Admin Overview + subscription + metrics alerts widgets | full admin Widget E2E |
| [L05](L05-employee-shell.md) | doing | 7 | [12](../12-layer-docs/L05-employee-shell.md) | Flutter SSE chat + attachment preview (image/text/PDF stub) | AppAuth redirect URIs; real PDF renderer |
| [L06](L06-cabinet-runtime.md) | done | 8 | [12](../12-layer-docs/L06-cabinet-runtime.md) | runtime API+MCP+bundle+packages | k8s sandbox |
| [L07](L07-projects-runtime.md) | doing | 7 | [12](../12-layer-docs/L07-projects-runtime.md) | project CRUD+materialize+outbox+CronJob examples | k8s pod; external broker |
| [L08](L08-agent-providers.md) | doing | 8 | [12](../12-layer-docs/L08-agent-providers.md) | port+budget+transcript+fixture | Node sidecar SDK |
| [L09](L09-vertical-integration.md) | doing | 7 | [12](../12-layer-docs/L09-vertical-integration.md) | E2E vertical + widget subset + release_gate_check | full Widget E2E |

## Фазы (сводка)

| Фаза | Условие выхода | Статус |
|------|----------------|--------|
| A фундамент (L00+L01+L02+L03 + API L06) | контракты C-* поставщиков зелёные | doing (L00/L02/L03/L06 done; L01 partial; L04 API started) |
| B control (L04+L05) | UX contracts 01/03/04 | doing (L04 Admin UI + L05 employee) |
| C execution (L07+L08) | materialize + AgentEvent | doing (L07+L08 API) |
| D vertical (L09) | E2E + metrics | doing (API smoke + Flutter chat subset) |

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
