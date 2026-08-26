# Cabinet — сущность (канон)

Кабинет — **оболочка рабочего пространства** + **meta/data**.  
Назначение сотрудникам — **Company** ([assignment.md](assignment.md)).  
Карта: [00-entities](../00-entities.md).

## Реестр (platform DB)

| Поле | Смысл |
|------|--------|
| `id` | `cab_*` |
| `name` | имя |
| `company_id` | орг-владелец |
| `schema_name` | PG schema (`cab_inst_…`) |
| `status` | active / archived / … |
| timestamps | |

Доступ сотрудников — через **assignment** (N:M), не единственный `owner_employee_id` как единственный ACL (owner-поле может остаться для audit).

## Meta + data (суть)

| Вид | Примеры |
|-----|---------|
| UI | tabs, views, columns |
| MCP | tool defs, packages |
| Промпты | AGENTS.md, rules, skills (MD / MinIO refs) |
| Файлы | `file_ref` → MinIO object key |
| Данные | обычные таблицы строк |

UI = интерпретатор meta. Сырой SQL от модели запрещён.  
Агент в Pod — только `cabinet.*` MCP ([mcp-contracts](mcp-contracts.md)).  
Файлы в Pod — [materialize-from-meta](materialize-from-meta.md).

## Связи и каскад

```text
Company ──assigns──► Employee ↔ Cabinet
Cabinet ──has──► Project (N) ──1:1──► ProjectContainer
```

**Delete Cabinet** → все Projects кабинета (всех сотрудников) wipe + Pod/MinIO + schema + grants.

## Не путать

| | |
|--|--|
| Cabinet | meta workspace shell |
| Project | единица работы в кабинете |
| ProjectContainer | k8s Pod |
| starter bundle | seed zip |

Дальше: [assignment](assignment.md) · [dynamic-cabinets](dynamic-cabinets.md) · [materialize](materialize-from-meta.md).
