# Companies — domain

## Семантика

Company = **org** control plane: сотрудники, квоты, метрики, org-ownership кабинетов сотрудников.  
UI: роль `company.admin`.

Кабинеты **динамические** ([05](../05-cabinets/dynamic-cabinets.md)): создаёт Employee; Company **владеет на уровне org** (метрики/policy), не раздаёт статические `profile_id` modules.

**Не** читает agent chat / rows кабинета по умолчанию (break-glass — отдельная политика).

## Сущности

| Сущность | Описание |
|----------|----------|
| `Company` | Org (создаёт Platform Admin) |
| `Employee` | Person + `keycloak_sub` + membership |
| `EmployeeStatus` | `invited` \| `active` \| `disabled` |
| `CabinetInstance` | Принадлежит employee + `company_id` (org ownership) |
| `CompanyAgentPolicy` | preferred_provider, platform_fallback, tool preset narrow |
| Quotas | Inherited/override from Admin |

**Устарело:** `EmployeeCabinetAssignment` ⊆ `CompanyCabinetGrant` по profile modules.

## Операции

| Операция | Инвариант |
|----------|-----------|
| `employee.invite` | Email → Keycloak; **без password** |
| `employee.disable` / `enable` | Disabled → 403 cabinet/project API |
| `metrics.employees` / `metrics.cabinets` | Aggregates; list cabinet names/owners read-only |
| `policy.narrow` | Company может только **сужать** Admin policy |

## Поток кабинетов

```text
Admin → company + quotas
Employee → create/import CabinetInstance (own schema)
Company admin → sees list/metrics; does not edit peer data
Peer employees → no access to each other's schemas
```
