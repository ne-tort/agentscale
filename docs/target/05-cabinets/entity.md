# Cabinet — сущность (канон)

Кабинет — **оболочка рабочего пространства** (registry) + **отдельный meta-слой**.  
Назначение сотрудникам — **Company** ([assignment.md](assignment.md)); MVP Admin привязывает кабинет к Company.  
Карта: [00-entities](../00-entities.md).

## Реестр (platform DB)

| Поле | Смысл |
|------|--------|
| `id` | `cab_*` |
| `name` | имя |
| `company_id` | орг-владелец (Admin create/bind) |
| `owner_employee_id` | nullable; audit / employee create |
| `schema_name` | PG schema (`cab_inst_…`) |
| `status` | active / archived / … |
| timestamps | |

Доступ сотрудников через **assignment** (N:M) — следующий этап после Admin CRUD ([09-gap-map](../09-gap-map.md)).

## Meta (отдельно от «ядра»)

MVP: таблица `meta_documents` в schema инстанса — `slug` + свободный **JSONB** `body`.  
Валидация только формата (object/array). Typed DDL, tabs/views/columns, MCP packages, starter bundles — **не** часть cabinet entity в MVP.

UI = будущий интерпретатор documents. Сырой SQL от модели запрещён.

## Связи и каскад

```text
Company ──binds──► Cabinet
Cabinet ──has──► Project (N) ──1:1──► ProjectContainer
```

**Delete Cabinet** (Admin) → wipe всех Projects кабинета + Pod/MinIO + DROP schema + delete row.

## Не путать

| | |
|--|--|
| Cabinet | registry + schema shell |
| meta_documents | free-form JSON workspace meta |
| Project | единица работы в кабинете |
| ProjectContainer | k8s Pod |

Дальше: [assignment](assignment.md) · [backend](backend.md) · [materialize](materialize-from-meta.md).
