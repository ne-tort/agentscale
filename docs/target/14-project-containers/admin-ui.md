# Project Containers — Admin UI

## Навигация Platform Admin

| Tab | Назначение |
|-----|------------|
| Обзор | metrics / alerts |
| Компании | org control plane |
| AI-ключи | credential inventory |
| **Контейнеры** | runtime isolators (этот модуль) |
| **Кабинеты** | stub (пока) |

**Убрать** tab «Бандлы» / `AdminStarterBundlesPage` из shell.  
Starter `cabinet.bundle` catalog остаётся в employee/import API ([05](../05-cabinets/bundle-format.md)), не в admin chrome.

## Список контейнеров

`AppEntityCollection` table; **сортировка: running / active сверху**, затем paused, failed, прочие.

| Column | Источник |
|--------|----------|
| Status | Container.status (или Project.status transitional P1) |
| Project | Project.name |
| Company | Company.name |
| Employee | Project owner display/email |
| Provider | Project.agent_provider / session resolve hint |
| Cabinet | CabinetInstance.name |

Узкие колонки — truncate + detail. Long-press mutate не обязателен на P1; actions на detail.

## Detail — группировка (подстраницы / секции)

Одна композиция с секциями (или sub-routes), без модалок:

1. **Runtime** — status, runtime_ref, image, last_error, k8s phase/restarts  
2. **Resources** — CPU/RAM usage vs limits; disk if available  
3. **Project** — id, status, links; Pause / Resume / Delete (каскад)  
4. **Org** — company, employee, cabinet (+ template/base if any)  
5. **AI** — preferred provider / last resolved key id (no secret)  
6. **Usage** — tokens / messages from agent_usage (related)  
7. **Ops** — Force kill, Reconcile  

Confirm для delete / force-kill: `AppConfirmPage` severity error; короткие тексты.

## P1 transitional UI

Пока нет ORM Container: list = projects join company/cabinet/owner; status из Project; `container_ref` как runtime_ref; metrics k8s — «—» / hole badge.  
Действия уже через Project API. Не показывать «только метаданные» бандлов.
