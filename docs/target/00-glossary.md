# Глоссарий (target)

Карта сущностей: **[00-entities.md](00-entities.md)**.  
Legacy — только маппинг.

---

## Identity (все с Keycloak)

| Термин | Определение |
|--------|-------------|
| **Platform Admin** | Оператор платформы; KC `platform.admin`. |
| **Company** | Организация + KC орг-аккаунт; **локальный Admin**-контур (сотрудники, контейнеры, свои AI keys). [03](03-companies/) |
| **Employee** | Человек с `keycloak_sub`; membership в Company (future: и без Company). |
| **Company account** *(переходный)* | As-built = Employee + `company.admin`. Цель — отдельный KC principal Company. |

---

## Домен

| Термин | Определение |
|--------|-------------|
| **Cabinet** | Оболочка workspace: реестр + **meta/data**. UI из meta. [05](05-cabinets/entity.md) |
| **Cabinet assignment** | Grant Company: Employee ↔ Cabinet. [assignment](05-cabinets/assignment.md) |
| **Base cabinet** | Шаблон (projects, chat, Tables, Tools + `cabinet.*`). |
| **Cabinet bundle** | Zip/json meta(+seed) для import. Не runtime. |
| **MCP package** | Zip; deploy через `cabinet.mcp_packages.deploy`. |
| **Meta** | tables/tabs/views/MCP defs/docs/file refs внутри cabinet schema. |
| **Project** | Единица работы: Employee в Cabinet; 1:1 Container. |
| **Project Container** | **k8s Pod** + workspace MinIO. [14](14-project-containers/). |
| **Materialize** | Meta (+ MinIO file ids) → workspace Pod. [materialize](05-cabinets/materialize-from-meta.md) |
| **Trigger** | Событие запуска/продолжения агента. |
| **AI Provider Key** | Ключ ИИ: `owner_scope=platform\|company`; Company видит bound platform RO. [02](02-ai-provider-keys/) |
| **provider** / **api_kind** | `cursor` \| `codex` \| … / `cursor_sdk` \| … |

Устарело: static `profile_id` code-packs; «только owner создал кабинет без company assign» как единственный ACL.

---

## UI / Runtime

| Термин | Определение |
|--------|-------------|
| **AppListItem** / **AppSelectorPage** | List / full-screen select. |
| **Modal ban** | Без dialog/sheet для выбора и confirm. |
| **AgentProviderPort** | create/resume/stream/cancel. |
| **Usage metrics** | Сотрудники, проекты, токены, storage… |
