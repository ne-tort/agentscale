# Cabinet — сущность (канон)

Кабинет — **оболочка рабочего пространства** (registry) + **отдельный meta-слой**.  
Назначение: N:M grants Admin→Company + Company→Employee ([assignment.md](assignment.md)).  
Карта: [00-entities](../00-entities.md) · Ownership: [00-ownership-matrix](../00-ownership-matrix.md).

## Реестр (platform DB)

| Поле | Смысл |
|------|--------|
| `id` | `cab_*` |
| `name` | имя |
| `owner_scope` | `platform` \| `company` |
| `owner_company_id` | creator company при `owner_scope=company` |
| `company_id` | legacy anchor (primary grant); nullable |
| `owner_employee_id` | nullable; audit / employee create |
| `schema_name` | PG schema (`cab_inst_…`) |
| `status` | active / archived / … |
| timestamps | |

### Grant tables

- `cabinet_company_grants` — N:M cabinet ↔ company (`mode`, `status`)
- `cabinet_employee_assignments` — N:M cabinet ↔ employee (`role`, `status`)

Доступ сотрудников через **assignment**; доступ компании через **company grant**.

## Meta (отдельно от «ядра»)

MVP: таблица `meta_documents` в schema инстанса — `slug` + свободный **JSONB** `body`.  
Platform-owned cabinets: Company/Employee **read** meta; **write** только Admin или company-owned.

## Связи и каскад

```text
Company ──grants (N:M)──► Cabinet
Cabinet ──has──► Project (N) ──1:1──► ProjectContainer
```

**Delete Cabinet** (Admin) → wipe всех Projects кабинета + Pod/MinIO + DROP schema + delete row + grants.

## Не путать

| | |
|--|--|
| Cabinet | registry + schema shell |
| meta_documents | free-form JSON workspace meta |
| Project | единица работы в кабинете |
| ProjectContainer | k8s Pod |

Дальше: [assignment](assignment.md) · [backend](backend.md) · [materialize](materialize-from-meta.md).
