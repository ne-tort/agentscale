# Сущности и иерархия (канон)

Карта продукта: кто есть кто, кто кого создаёт, как связаны, что каскадируется.  
**Код может отставать** — расхождения: [09-gap-map](09-gap-map.md). Этот файл = **как должно**.

## Поток (создание и работа)

```text
Platform Admin (Keycloak)
  └── создаёт Company (Keycloak-креды орг-аккаунта)
        ├── создаёт Employee (Keycloak)
        ├── назначает Employee → CabinetInstance workspace(s)
        └── …
              Employee работает в UI кабинета (UI из meta)
                └── создаёт Project (связь: Cabinet; creator = metadata)
                      └── ProjectContainer = k8s Pod
                            ├── hydrate файлов из MinIO (по meta)
                            └── агент ↔ meta через cabinet.* MCP
```

Каждая сущность **изолирована** (свой контур, свои ACL), но **иерархически связана**.

## Company = локальный Admin

Контур Company **зеркалит** Platform Admin IA, scope = своя org:

| Admin | Company (тот же паттерн) |
|-------|--------------------------|
| Компании | Сотрудники |
| AI Keys (platform) | AI Keys **свои** (CRUD) + Admin-linked (**RO**) |
| Контейнеры (все) | Контейнеры **своих** сотрудников |
| Кабинеты (надзор / assign) | Кабинеты, что Admin выдал компании — **RO** (MVP); later — свои local |

Фокус выравнивания: [03-companies](03-companies/). Keys: [02](02-ai-provider-keys/).

## Универсальное владение (future)

Сущности независимы; связи — grants, не жёсткое «только через Company»:

```text
Admin ──может──► Company | Employee (локальные, без company) | AiProviderKey | Cabinet
Company ──может──► Employee | AiProviderKey(local) | Cabinet(local, later) | assign ↓
Любой control plane ──assign──► keys / cabinets / projects в своём scope
```

MVP путь остаётся Admin → Company → Employee.  
Прямые Admin→Employee и local Company cabinets — **после** паритета Company shell (**P-UNI-01**).

## Identity: у кого Keycloak

| Сущность | Keycloak | Зачем |
|----------|----------|--------|
| **Platform Admin** | Да (`platform.admin`) | Платформенный control plane: компании, keys, контейнеры, кабинеты |
| **Company** | Да (орг-аккаунт / company principal) | **Локальный Admin**: сотрудники, их контейнеры, свои AI keys, кабинеты (RO от Admin) |
| **Employee** | Да (`keycloak_sub`) | Работа в назначенных кабинетах и проектах |

Пароли и логины — **только Keycloak**. Prodavan API не хранит password и не принимает его на create/invite.

Детали сессии: [10-identity-keycloak/session.md](10-identity-keycloak/session.md).

## Связи (граф)

```text
Admin ──creates──► Company ──creates──► Employee
                      │                    │
                      │ assigns            │ operates
                      ▼                    ▼
                 CabinetInstance ◄─────────┘
                      │
                      │ N:M (module catalog)
                      ▼
                   Module
                      │
                      │ scope (N projects, via cabinet grant)
                      ▼
                   Project ──owner──► Employee
                      │
                      │ 1:1
                      ▼
               ProjectContainer (Pod)
```

| Связь | Кардинальность | Смысл |
|-------|----------------|--------|
| Admin → Company | 1:N | provisioning |
| Company → Employee | 1:N | invite / membership |
| Company → Cabinet | 1:N | org ownership |
| Company assigns Employee ↔ Cabinet | N:M | кто **может** работать в каком кабинете |
| Employee → Project | 1:N | создатель / оператор (metadata); restricted visibility via assignment |
| Cabinet → Project | 1:N | проект живёт **в** кабинете |
| Project → ProjectRuntimeUnit | 1:N | 0..N isolators; Pod link optional |

Peers: Employee A не видит Cabinet/Project/Pod Employee B, если нет явного assign на тот же cabinet (по умолчанию peer isolation).

## Lifecycle (pause · soft-delete · purge)

Канон двух осей: [00-lifecycle.md](00-lifecycle.md).

| | Pause (виден) | Soft-delete (скрыт) | Hard-purge |
|--|---------------|---------------------|------------|
| **Company** | org pause → cascade pause projects | `deleted_at` → cascade soft children, **без wipe** | wipe/DROP/KC после soft |
| **Employee** | `disabled` (= paused) | `deleted_at` | row + KC |
| **Cabinet** | `archived` | `status=deleted` | DROP schema + purge projects |
| **Project** | `paused` \| `completed` | `status=deleted`, blobs **keep** | wipe MinIO |

**Restore** только целевой сущности → live+paused; **без** cascade revive. Soft никогда не уничтожает данные.

### Каскады soft-delete

| Удалить | Эффект вниз |
|---------|-------------|
| **Company** soft-delete | UI hide → Kafka `company.deleted` → Auth KC delete → soft_delete employees / projects / cabinets (stop pods, **no wipe**; Celery). |
| **Cabinet** soft-delete | soft_delete projects (stop pods); schema **keep** |
| **Project** soft-delete | stop sessions/Pod; blobs keep |
| **Employee** pause/soft-delete | 403 / hide; Auth disable; **не** auto-wipe projects |
| **\* purge** | wipe MinIO / DROP / GC — явная операция |

### Async topology (REST / Kafka / Celery)

| Роль | Кто |
|------|-----|
| REST | BC — CRUD / pause / soft_delete / restore / purge, publish после commit |
| Kafka | Контракт (`company.deleted`, `project.deleted`, Auth commands, …) |
| Kafka consumer | In-process `KafkaManager` в API (не Celery) |
| Celery | Wipe MinIO, DROP schema, pod stop, cascade, bind `keycloak_sub` |

`keycloak_sub IS NULL` (и сущность **live**) = зомби → admin alert. Soft-deleted из unbound-count исключаются.

## Сущности (кратко)

| Сущность | Что | Где правда |
|----------|-----|------------|
| **Platform Admin** | Оператор платформы | Keycloak + Admin UI |
| **Company** | Организация + **свой** KC-логин | platform DB + Keycloak |
| **Employee** | Человек в компании | platform DB + Keycloak |
| **CabinetInstance** | Оболочка рабочего пространства | реестр в DB; суть в **meta/data** |
| **Module** | Переиспользуемый каталог meta (tables/columns/views) | platform DB; N:M cabinet + project |
| **Project** | Единица работы агента | platform DB (`cabinet_id` + `created_by_employee_id`); BC `project_service` |
| **ProjectRuntimeUnit** | Изолированный runtime (Pod optional) | DB row + optional Pod + MinIO |
| **AgentSession** | Сессия в Project | platform DB |

## Cabinet = оболочка + meta

Реестр (мало полей): id, name, company_id, status, schema_name, timestamps (+ assignment links).

Вся суть — **meta/data** (JSONB / таблицы в schema instance), в том числе:

| Вид meta | Примеры | Кто трогает |
|----------|---------|-------------|
| UI-структура | tabs, views, columns | UI + `cabinet.*` MCP |
| MCP defs | tool definitions, packages | UI + MCP |
| Промпты / агент-доки | `AGENTS.md`, rules, skills (MD + refs) | UI + MCP |
| Файлы | file id / object key → **MinIO** | UI + MCP |
| Данные | обычные строки таблиц | UI + MCP |

UI кабинета = **интерпретатор** meta (не hardcoded Flutter-домен).

Подробнее: [05-cabinets/entity.md](05-cabinets/entity.md), [assignment](05-cabinets/assignment.md), [materialize](05-cabinets/materialize-from-meta.md).

## ProjectContainer и meta

При **create / resume** Pod:

1. Platform читает meta кабинета (нужные docs, packages, file refs).
2. Собирает/обновляет объекты в **MinIO** `projects/{workspace_key}/…`.
3. **Hydrate** в Pod `/workspace` (копия нужных файлов).
4. Агент в Pod ходит в meta **только** через scoped `cabinet.*` MCP (строгий контракт): читать/писать data, при разрешении — создавать meta-объекты, которые UI затем отрисует.

Нет произвольного SQL к platform DB из Pod. Нет доступа к peer Pod / Postgres / Redis / Kafka / Keycloak admin.

Канон isolator: [14-project-containers](14-project-containers/).

## Чтение дальше

1. [Identity session](10-identity-keycloak/session.md)  
2. [Companies](03-companies/) · [Employees](04-employees/)  
3. [Cabinets](05-cabinets/) · [Modules](06-modules/) · [Projects](06-projects-runtime/) · [Containers](14-project-containers/)  
4. [Gap / проблемы](09-gap-map.md)  
