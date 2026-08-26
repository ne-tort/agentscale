# Cabinet assignment (канон)

Два уровня grants (N:M). Кабинет = оболочка workspace.

Иерархия: [00-entities](../00-entities.md). Ownership: [00-ownership-matrix](../00-ownership-matrix.md).

## Модель

```text
Admin ──grants (N:M)──► Cabinet ◄──grants (N:M)──► Company     (Company видит RO если platform-owned)
Company ──assigns (N:M)──► Employee ↔ Cabinet              (операторский доступ)
Employee ──operates──► Cabinet → Project → Pod

Future:
  Company ──creates/copies──► own local Cabinets (полный manage)
  Admin ──assigns──► Cabinet/Employee напрямую (универсальная иерархия)
```

| Правило | MVP | Future |
|---------|-----|--------|
| Admin → Company cabinet | N:M grants; Company **RO** meta if platform-owned | + revoke |
| Company local cabinets | employee create → `owner_scope=company` | create / copy / CRUD своих |
| Company → Employee grant | **да** (на cabinets с active company grant) | то же |
| Employee operate / projects | только с active assignment | то же |

## Grant Admin→Company (`cabinet_company_grants`)

| Поле | Смысл |
|------|--------|
| `cabinet_id` | кабинет |
| `company_id` | компания |
| `mode` | `assigned_ro` (MVP) / later `owned_local` |
| `status` | active / revoked |
| timestamps | |

UNIQUE (`cabinet_id`, `company_id`). CASCADE от cabinet.

## Grant Employee↔Cabinet (`cabinet_employee_assignments`)

| Поле | Смысл |
|------|--------|
| `cabinet_id` | кабинет |
| `employee_id` | сотрудник |
| `role` | `operator` (MVP) / later `viewer` |
| `status` | active / revoked |
| timestamps | |

UNIQUE (`cabinet_id`, `employee_id`). CASCADE от cabinet / employee.

## Cascade

**Delete Cabinet** → все Projects → wipe Pods/MinIO → schema → все grants → cabinet.

## Не канон

- Static `profile_id` code-pack grants.
- As-built «только owner_employee» без company/admin assign.
- Single `company_id` FK as sole ACL (legacy; backfilled into grants).
