# Gap map — target ↔ код

> Канон (как должно): **[00-entities.md](00-entities.md)**.  
> Код / as-built: [12-layer-docs](12-layer-docs/), [STUB.md](../../STUB.md).  
> Этот файл — **расхождения = проблемы**, не «канон подстроили под stub».

## Проблемы vs канон сущностей (P0 product / runtime)

Проверка as-built (API + Flutter + docs/12) относительно [00-entities](00-entities.md):

| ID | Канон | Сейчас в коде | Проблема |
|----|-------|---------------|----------|
| **P-CO-01** | Company shell = **локальный Admin** (сотрудники, контейнеры, keys, кабинеты) | 3 tabs: metrics / employees / cabinets RO; нет Keys/Containers | Тонкий org-shell ≠ Admin parity ([03](03-companies/)) |
| **P-CO-02** | Company **CRUD своих** AI keys (SDK/API) + видит Admin-bound **RO** | API `/companies/{id}/ai-keys` + `owner_scope` (**partial**); Flutter tab — open | Backend link live; UI later |
| **P-CO-03** | Company list/manage containers **своих** сотрудников | Только Admin `/admin/containers` | Нет company-scoped containers |
| **P-CO-04** | Cabinets от Admin → Company **RO**; later local CRUD | **Admin CRUD + N:M company grants**; Company RO + employee assign | Company assign UI shipped; Verify Dev |
| **P-ID-01** | **Company** имеет **Keycloak-креды** | `companies.keycloak_sub` via Auth Kafka `auth.user.register` + bind; soft-delete `deleted_at` + async cascade | Org principal async; zombies in admin metrics |
| **P-CAS-02** | Company soft-delete → soft children (no wipe) | Soft-delete + Celery cascade; wipe only on purge | Align cascade to [00-lifecycle](00-lifecycle.md) |
| **P-LC-01** | Unified pause / soft_delete / purge + restore | Partial (company deleted_at; project pause/delete wipe) | Recycle API; project soft without wipe |
| **P-REL-01** | Central Relations BC: query + Kafka grant/revoke | Facade `RelationsQuery`/`RelationsCommand` + `prodavan.relation.events`; ACL reads migrated; physical consolidate later | Soft-status unify; AI/module writes via RelationsCommand |
| **P-ID-02** | Admin / Company / Employee — три KC-сущности | Realm roles + `prodavan-keycloak-init` bootstrap; API resolution live | IdP brokers / SMTP invite polish |
| **P-CAB-01** | Company **назначает** Employee ↔ Cabinet | **Grants + assignment API + Flutter** | Verify Dev E2E |
| **P-CAB-02** | UI кабинета из module meta | Module template + `module_data_rows` API; employee UI = placeholder | Generic meta UI next |
| **P-MOD-01** | **Module** catalog + cabinet bind + per-cabinet data | **Admin CRUD + meta + materialize + runtime data API + Flutter** | Physical DDL; meta editor UI |
| **P-MOD-02** | Meta-table **syntax** spec + interpreters | **[meta-syntax](06-modules/meta-syntax/) documented**; shell `nav.contour` preview in seed editor | Live catalog shell merge; cabinet UI renderer; materialize engine |
| **P-MAT-01** | Pod hydrate из meta/MinIO | object-ws; нет Pod; file_ref слаб | Materialize/Pod debt |
| **P-POD-01** | `ProjectContainer` = k8s Pod; inert → delete Pod | `object-ws:…`; pause stub `pod_stop` desired | [14](14-project-containers/) |
| **P-MCP-01** | Агент в Pod ↔ `cabinet.*` | **Out of MVP cabinet entity** (removed typed MCP/packages); future contract | Изоляция + контракт |
| **P-CAS-01** | Cabinet soft_delete → soft projects; purge → DROP | Admin hard `delete_with_cascade` | Soft default DELETE + purge |
| **P-INF-01** | MinIO / Kafka / Celery | Local FS / in-process | [13](13-platform-infra/) |
| **P-KC-01** | Live Keycloak cutover | **Auth Service BFF** + `AUTH_MODE=oidc`; Flutter never → KC | Brokers UI / SMTP invite polish |
| **P-KC-02** | IdP broker live (VK/Yandex) | Auth Service start/callback ready; providers **not** live; secrets вне git | Enable IdP + Flutter social buttons |
| **P-UNI-01** | Универсальная иерархия (Admin→Employee без Company; local cabinets) | Не моделировано | Future после P-CO-* |

### Что уже близко к канону

| Тема | Статус |
|------|--------|
| Invite employees (Company) | есть (тонкий UI) |
| Org cabinets list RO | Admin list + company bind (MVP); Company shell RO still employee-sourced |
| Admin keys + containers | есть |
| Company metrics aggregates | есть |

## P0 — Platform infra

Канон: [13-platform-infra/](13-platform-infra/). План: [P0-platform-infra](11-implementation-plan/P0-platform-infra.md).

| Gap | Сейчас | Цель |
|-----|--------|------|
| Local workspace FS | `data/storage`, `local-ws` | **MinIO** |
| In-process workers | asyncio в API | **Celery** + Redis |
| Нет event bus | PG outbox-lite | **Kafka** |
| Lifespan ad-hoc | `main.py` | `LifespanManager` + infra managers |

## Прочие gaps (модули)

| Target | Код сейчас | Gap |
|--------|------------|-----|
| Platform Admin UI + metrics | partial / stub | [01 ux](01-platform-admin/ux-contract.md) |
| AI Provider Keys | partial | [02](02-ai-provider-keys/) |
| Mobile UI, no modals | Theme + core; feature shells | [07](07-ui-mobile-core/) |
| AgentProviderPort | stub / partial | [08](08-agent-providers/) |
| OpenClaw / GLM | нет | **не внедрять** |

## Бывшие «решения канона» → пересмотр

Старые строки, которые **больше не цель** (заменены [00-entities](00-entities.md)):

| Было зафиксировано | Теперь |
|--------------------|--------|
| Company = org без login; UI = Employee + `company.admin` | Company = org **+ KC** + **локальный Admin shell** (**P-CO-01**, **P-ID-01**) |
| CompanyApi не ведёт Projects / Keys | Company **ведёт** containers + **свои** AI keys (**P-CO-02/03**) |
| Employee сам создаёт cabinets; Company только metrics | Admin CRUD + company bind (MVP); Company→Employee assign next (**P-CO-04**, **P-CAB-01**) |
| Keys только Admin inventory + bindings | `owner_scope` platform \| company (**P-CO-02**) |

Остаётся в силе:

| Тема | Решение |
|------|---------|
| Dynamic cabinets (не static code-packs) | да |
| Password только в Keycloak | да |
| `cli_subscription` ≠ runtime credential | да |
| Admin metrics ← `AgentEvent.usage` | да |

## Декомпозиция BC

```mermaid
flowchart TB
  subgraph identity [Identity_Keycloak]
    KC[Keycloak]
  end
  subgraph control [Control_plane]
    Admin[PlatformAdmin]
    Keys[AiProviderKeys]
  end
  subgraph org [Org_plane]
    Company[Company_KC]
    Employees[Employees_KC]
  end
  subgraph work [Work_plane]
    CabInst[CabinetInstance]
    Assign[CabinetAssignment]
    Project[Project]
    Pod[ProjectContainer_Pod]
    Agent[Agent_in_Pod]
  end

  KC --> Admin
  KC --> Company
  KC --> Employees
  Admin --> Company
  Company --> Employees
  Company --> CabInst
  Company --> Assign
  Assign --> Employees
  Assign --> CabInst
  Employees --> Project
  CabInst --> Project
  Project --> Pod
  CabInst -->|"materialize meta"| Pod
  Pod --> Agent
  Agent -->|"cabinet.* MCP"| CabInst
  Keys --> Agent
```

| BC | Ответственность | Запрещено |
|----|-----------------|-----------|
| Company | Локальный Admin: employees, containers, **company keys**, assign cabinets, policy narrow | Issue JWT; edit platform keys; peer schema без grant |
| Admin + Keys | Companies (+KC), platform keys + bind, quotas, metrics, assign cabinets→company | Workspace files |
| Cabinet Runtime | Registry + module data rows; Admin modules | Typed meta UI / MCP packages / Pod lifecycle |
| Projects / Containers | Project, Pod port, triggers, materialize hydrate | Hardcoded domain packs |
| Agent | Port + adapters in Pod | GLM, OpenClaw |
| UI core | Primitives | Feature ListTile zoos |

### Волны (ориентир) — сначала Company parity

1. **P-CO-01..04** + **P-CO-02** model `owner_scope` — Company shell = Admin-like ([03](03-companies/))  
2. **P-ID-01 / P-ID-02** — Company KC principal  
3. **P-CAB-01** — Employee↔Cabinet grants  
4. **P-INF-01 / P-POD-01 / P-MAT-01** — MinIO + Pod  
5. **P-UNI-01** — универсальная иерархия (после паритета Company)  
6. Остальное — L04–L08 по [11](11-implementation-plan/)

### Явно не делать

- OpenClaw; GLM; personal Max/Pro как tenant runtime credentials  
- Канонизация `POST /auth/login`  
- Static `profile_id` code-packs  
- JWT reissue на switch/open  
- Подгонять канон под object-ws «как контейнер»

## Ссылки

- [00-entities](00-entities.md) · [03 Companies](03-companies/) · [02 Keys](02-ai-provider-keys/)  
- [05 assignment](05-cabinets/assignment.md) · [14 containers](14-project-containers/) · [10 session](10-identity-keycloak/session.md)
