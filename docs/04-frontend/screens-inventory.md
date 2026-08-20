# Инвентарь экранов M00–M09

Полный перечень экранов Flutter-клиента по модулям платформы. Навигация между модулями — **icon-only rail** (см. [design-system.md](design-system.md)); название модуля — в `AppScaffold.title`.

---

## Легенда

| Колонка | Значение |
|---------|----------|
| Route | Относительно `/cabinet/{cid}/` |
| Nav | Иконка в rail (если top-level) |
| Capability | Требуемый flag из manifest |
| Min role | Минимальная роль cabinet |

---

## M00 — Кабинеты

| # | Экран | Route | Nav | Capability | Min role |
|---|-------|-------|-----|------------|----------|
| M00-01 | Селектор кабинета (header) | — | — | — | viewer |
| M00-02 | Wizard создания кабинета | `/settings/cabinets/new` | M00 | — | tenant.admin |
| M00-03 | Настройки кабинета | `/settings/cabinet` | M00 | — | cabinet.admin |
| M00-04 | Архивные кабинеты | `/settings/cabinets/archived` | M00 | — | tenant.admin |
| M00-05 | Capabilities (read-only) | tab в M00-03 | — | — | viewer |

---

## M01 — Проекты

| # | Экран | Route | Nav | Capability | Min role |
|---|-------|-------|-----|------------|----------|
| M01-01 | Список проектов | `/projects` | M01 | `projects` | viewer |
| M01-02 | Создание проекта | `/projects/new` | — | `projects` | operator |
| M01-03 | Карточка проекта | `/projects/{slug}` | — | `projects` | viewer |
| M01-04 | Inbox / вложения | `/projects/{slug}/inbox` | — | `projects` | operator |
| M01-05 | Архив проектов | `/projects/archived` | — | `projects` | admin |

---

## M02 — Спеки и КП

| # | Экран | Route | Nav | Capability | Min role |
|---|-------|-------|-----|------------|----------|
| M02-01 | Прогоны проекта | `/specs` или tab проекта | M02 | `specs_kp` | viewer |
| M02-02 | Детали прогона | `/specs/runs/{runId}` | — | `specs_kp` | viewer |
| M02-03 | Позиции (line items) | `/specs/runs/{runId}/items` | — | `specs_kp` | viewer |
| M02-04 | Варианты / офферы | `/specs/runs/{runId}/variants` | — | `specs_kp` | operator |
| M02-05 | Equipment cards | `/specs/equipment` | — | `equipment_cards` | viewer |
| M02-06 | Экспорт КП | modal / action | — | `kp.export` | operator |
| M02-07 | Review gate (final) | `/specs/runs/{runId}/review` | — | `specs_kp` | operator |

---

## M03 — Промпты

| # | Экран | Route | Nav | Capability | Min role |
|---|-------|-------|-----|------------|----------|
| M03-01 | Дерево документов | `/prompts` | M03 | `prompts_edit` | admin |
| M03-02 | MD-редактор AGENTS.md | `/prompts/agents` | — | `prompts_edit` | admin |
| M03-03 | Profiles list | `/prompts/profiles` | — | `prompts_edit` | admin |
| M03-04 | Profile module editor | `/prompts/profiles/{id}/{module}` | — | `prompts_edit` | admin |
| M03-05 | Diff / history | `/prompts/history/{docId}` | — | `prompts_edit` | admin |

Подробнее: [md-editor.md](md-editor.md).

---

## M04 — Каталоги

| # | Экран | Route | Nav | Capability | Min role |
|---|-------|-------|-----|------------|----------|
| M04-01 | Список БД каталогов | `/catalogs` | M04 | `catalogs_user` | viewer |
| M04-02 | Import wizard | `/catalogs/import` | — | `catalogs_user` | admin |
| M04-03 | Query console | `/catalogs/{name}/query` | — | `catalogs_user` | operator |
| M04-04 | System S4B cache | `/catalogs/system/s4b-cache` | — | `catalogs_system_s4b` | viewer |

---

## M05 — Интеграции

| # | Экран | Route | Nav | Capability | Min role |
|---|-------|-------|-----|------------|----------|
| M05-01 | Обзор интеграций | `/settings/integrations` | M05 | `integrations` | viewer |
| M05-02 | S4B settings | `/settings/integrations/s4b` | — | `s4b` | admin |
| M05-03 | Trusted sellers | tab M05-02 | — | `s4b` | admin |
| M05-04 | Web shops allowlist | `/settings/integrations/web-shops` | — | `web_shops` | admin |
| M05-05 | Rate limits | `/settings/integrations/limits` | — | `integrations` | admin |

---

## M06 — MCP

| # | Экран | Route | Nav | Capability | Min role |
|---|-------|-------|-----|------------|----------|
| M06-01 | Установленные серверы | `/mcp` | M06 | `mcp_admin` | admin |
| M06-02 | Tool catalog | `/mcp/{serverId}/tools` | — | `mcp_admin` | admin |
| M06-03 | Agent profiles | `/mcp/profiles` | — | `mcp_admin` | admin |
| M06-04 | Session bindings (read-only) | `/mcp/sessions` | — | `mcp_admin` | admin |

---

## M07 — Агент

| # | Экран | Route | Nav | Capability | Min role |
|---|-------|-------|-----|------------|----------|
| M07-01 | Project chat | `/projects/{slug}/chat` | M07 | `agent_chat` | operator |
| M07-02 | Debug drawer | panel в M07-01 | — | `agent_debug` | admin |
| M07-03 | Session history | `/projects/{slug}/chat/history` | — | `agent_chat` | viewer |
| M07-04 | Attachment sidebar | panel в M07-01 | — | `agent_chat` | operator |

---

## M08 — Tenants / пользователи

| # | Экран | Route | Nav | Capability | Min role |
|---|-------|-------|-----|------------|----------|
| M08-01 | Tenant settings | `/admin/tenant` | M08 | `tenant_admin` | tenant.admin |
| M08-02 | Users list | `/admin/users` | M08 | `tenant_admin` | tenant.admin |
| M08-03 | Invites | `/admin/invites` | — | `tenant_admin` | tenant.admin |
| M08-04 | Cabinet memberships | `/admin/cabinets/{cid}/members` | — | `tenant_admin` | tenant.admin |
| M08-05 | Billing / plan | `/admin/billing` | — | `tenant_admin` | tenant.owner |

---

## M09 — Operations

| # | Экран | Route | Nav | Capability | Min role |
|---|-------|-------|-----|------------|----------|
| M09-01 | Audit log | `/ops/audit` | M09 | `ops_view` | admin |
| M09-02 | Integration metrics | `/ops/integrations` | — | `ops_view` | admin |
| M09-03 | Agent sessions monitor | `/ops/sessions` | — | `ops_view` | admin |
| M09-04 | Storage quotas | `/ops/storage` | — | `ops_view` | admin |
| M09-05 | Health dashboard | `/ops/health` | — | `ops_view` | platform.admin |

---

## Icon nav map

```text
Rail (top → bottom, filtered by NavGate):

[M00] domain      — только admin+
[M01] folder      — всегда
[M02] receipt     — specs_kp
[M03] edit_note   — prompts_edit
[M04] storage     — catalogs_user
[M05] hub         — integrations
[M06] extension   — mcp_admin
[M07] smart_toy   — agent_chat (context: inside project)
[M08] people      — tenant_admin
[M09] monitor     — ops_view
```

M07 в rail ведёт на **последний активный проект** или empty state «Выберите проект».

---

## Empty states (cross-module)

| Экран | Empty state |
|-------|-------------|
| M01-01 | «Создайте первый проект» |
| M02-01 | «Загрузите спеку в inbox» |
| M04-01 | «Импортируйте прайс или подключите S4B» |
| M07-01 | «Выберите проект для чата с агентом» |

---

## Mobile vs desktop

| Экран | Mobile | Desktop |
|-------|--------|---------|
| Nav | Bottom bar (5 + overflow) | Icon rail 48px |
| M02 variants | Full screen list | Split pane |
| M03 editor | Single pane + preview tab | Split pane |
| M07 chat | Full screen | Split: chat + inbox sidebar |

См. [responsive.md](responsive.md).

---

## Связанные документы

- [cabinet-shell.md](cabinet-shell.md)
- [architecture.md](architecture.md)
- [../03-modules/](../03-modules/) — детальные ui.md per module
