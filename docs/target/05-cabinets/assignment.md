# Cabinet assignment (канон)

Два уровня grants. Кабинет = оболочка workspace.

Иерархия: [00-entities](../00-entities.md). Company shell: [03](../03-companies/).

## Модель

```text
Admin ──assigns (MVP)──► Cabinet ──to──► Company     (Company видит RO)
Company ──assigns──► Employee ↔ Cabinet              (операторский доступ)
Employee ──operates──► Cabinet → Project → Pod

Future:
  Company ──creates/copies──► own local Cabinets (полный manage)
  Admin ──assigns──► Cabinet/Employee напрямую (универсальная иерархия)
```

| Правило | MVP | Future |
|---------|-----|--------|
| Admin → Company cabinet | Assign / seed; Company **RO** (не edit meta) | то же + revoke |
| Company local cabinets | **нет** | create / copy / CRUD своих |
| Company → Employee grant | **да** (на доступные компании cabinets) | то же |
| Employee operate / projects | только с active assignment | то же |

## Grant Employee↔Cabinet

| Поле | Смысл |
|------|--------|
| `cabinet_id` | кабинет |
| `employee_id` | сотрудник |
| `role` | `operator` (MVP) / later `viewer` |
| `status` | active / revoked |
| timestamps | |

## Grant Admin→Company (cabinet visibility)

| Поле | Смысл |
|------|--------|
| `cabinet_id` | кабинет |
| `company_id` | компания |
| `mode` | `assigned_ro` (MVP) / later `owned_local` |
| timestamps | |

## Cascade

**Delete Cabinet** → все Projects → wipe Pods/MinIO → schema → все grants → cabinet.

## Не канон

- Static `profile_id` code-pack grants.
- As-built «только owner_employee» без company/admin assign.
