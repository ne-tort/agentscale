# Cabinet assignment (канон)

Кабинет = **общее рабочее пространство** для сотрудников компании.  
Иерархия: [00-entities](../00-entities.md). Ownership: [00-ownership-matrix](../00-ownership-matrix.md).

## Модель

```text
Admin ──создаёт шаблон (platform cabinet)
Admin ──grants (N:M)──► Company  →  provision workspace copy (per company)
Company ──assigns (N:M)──► Employee ↔ workspace Cabinet
Employee (в кабинете) ──видит все Projects кабинета──► Project → Pod
```

| Правило | MVP |
|---------|-----|
| Admin → Company | grant на **шаблон**; платформа материализует **копию** workspace (`template_cabinet_id`) |
| Company UI | список **workspace copies** (`owner_scope=company`); шаблон platform не показывается |
| Admin-provisioned workspace | Company **RO** registry (`writable=false`, `source=platform_assigned`) |
| Company local workspace | employee create → `owner_scope=company`, без `template_cabinet_id`, full manage |
| Company → Employee grant | на workspace cabinet с active company grant |
| Projects | только `cabinet_id`; creator = metadata (`created_by_employee_id` в API/events) |

## Grant Admin→Company (`cabinet_company_grants`)

На **шаблоне**: список компаний, которым выдан workspace.  
На **копии**: grant компании-владельца.

| Поле | Смысл |
|------|--------|
| `cabinet_id` | шаблон или workspace |
| `company_id` | компания |
| `mode` | `assigned_ro` (MVP) |
| `status` | active / revoked |
| timestamps | |

UNIQUE (`cabinet_id`, `company_id`). CASCADE от cabinet.

## Grant Employee↔Cabinet (`cabinet_employee_assignments`)

| Поле | Смысл |
|------|--------|
| `cabinet_id` | workspace |
| `employee_id` | сотрудник |
| `role` | `operator` (MVP) |
| `status` | active / revoked |
| timestamps | |

UNIQUE (`cabinet_id`, `employee_id`). CASCADE от cabinet; employee revoke не удаляет проекты.

## Cascade

| Событие | Эффект |
|---------|--------|
| Delete Employee (soft) | проекты **остаются**; `created_by_employee_id` → NULL |
| Delete Cabinet (soft) | soft_delete всех проектов кабинета; schema keep |
| Purge Cabinet | wipe soft-deleted projects + DROP schema + delete row |

## Не канон

- Один platform cabinet shared между компаниями для operate/projects.
- Project ownership через `owner_employee_id` (ACL).
- Static `profile_id` code-pack grants.
