# Companies — domain

**Выбранная сущность для выравнивания с каноном: Company.**

Company = **локальный control plane** по образцу Platform Admin, но в scope своей организации: сотрудники, их проекты/контейнеры, AI-ключи компании, кабинеты (сейчас RO от Admin).

Карта: [00-entities](../00-entities.md). UI: [ux-contract](ux-contract.md).

## Аналогия Admin ↔ Company

| Capability | Platform Admin | Company |
|------------|----------------|---------|
| Управление «нижестоящими» | Companies (+ future: employees без org) | **Свои Employees** |
| AI Keys | Platform-owned + bind к компаниям | **Свои** company-owned (полный CRUD); **Admin-linked** — видно, **RO** |
| Projects / Containers | Все на платформе | Только своих сотрудников |
| Cabinets | Assign / oversee компаниям | **Admin-assigned** — видно, **RO** (MVP); later — свои local CRUD |
| Не управляет | Employee chat/rows без break-glass | То же в своём org |

Company **не** создаёт другие компании. Company **не** правит platform-owned keys.

## Identity

Company имеет **Keycloak орг-аккаунт** ([session](../10-identity-keycloak/session.md)).  
Переходный as-built: вход через Employee + `company.admin` — debt (**P-ID-01**).

## Сущности

| Сущность | Описание |
|----------|----------|
| `Company` | Org + KC principal |
| `Employee` | Сотрудник org (KC); создаёт Company |
| `CabinetInstance` | Workspace shell; org scope |
| `CabinetAssignment` | Employee ↔ Cabinet (Company назначает операторам) |
| `Project` / `ProjectContainer` | Работа сотрудников; Company видит и управляет lifecycle в scope |
| `AiProviderKey` | `owner_scope=company` + `owner_company_id` — local; либо platform + binding — RO для Company |
| `CompanyAiKeyBinding` | Admin привязал platform key → Company видит RO |
| Quotas / policy | Admin задаёт потолок; Company может **сужать** |

## Операции (канон)

| Операция | Инвариант |
|----------|-----------|
| `employee.invite` / enable / disable | Как Admin для людей, только в своём `company_id` |
| `employee.assign_cabinet` / revoke | Grant на cabinet |
| `containers.list` / pause / resume / delete | Все projects/pods сотрудников компании; те же действия, что Admin, scoped |
| `ai_key.create` / update / rotate / delete | Только `owner_scope=company` и свой `owner_company_id` (вкл. SDK kinds) |
| `ai_key.list` | **Union**: свои local + Admin-bound (bound = RO в UI/API write) |
| `cabinets.list` | Admin-assigned (+ later own local); MVP write на admin-assigned **запрещён** |
| `cabinets.create` / copy / manage local | **Future** (P-CAB-LOCAL) |
| `metrics.*` | Org aggregates |
| `policy.narrow` | Только сужать Admin |

### Cascade / lifecycle

См. [00-lifecycle.md](../00-lifecycle.md).

| Событие | Эффект |
|---------|--------|
| Company soft-delete (Admin) | `deleted_at` + Kafka `company.deleted` → Auth KC delete → soft_delete employees / projects / cabinets (**stop pods, no wipe**). UI скрывает сразу. Restore — individually, без cascade revive. |
| Company purge | после soft; wipe children + DROP + KC GC |
| Cabinet soft-delete | soft_delete projects; schema keep |
| Cabinet purge | wipe projects + DROP schema |
| Employee disable (pause) | visible + Auth disable; **без** wipe projects |
| Employee soft-delete | hidden (`deleted_at`); **без** wipe projects |

BC Companies (`application/companies`) — REST org CRUD; не вызывает Keycloak Admin напрямую (только Auth Kafka). См. [10-identity-keycloak/architecture.md](../10-identity-keycloak/architecture.md), [13-platform-infra/principles.md](../13-platform-infra/principles.md) §3a.

## Будущее: универсальная иерархия

См. [00-entities §Универсальное владение](../00-entities.md).  
Admin сможет вести **локальных Employee без Company**; assign keys/cabinets любому principal.  
Company останется тем же паттерном control plane на своём уровне.
