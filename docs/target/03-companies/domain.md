# Companies — domain

## Семантика

Company = **org** (control plane для сотрудников и grants).  
Открывается пользователем с ролью `company.admin` (Company account = тот же Employee + роль).

**Не** создаёт кабинеты вне Admin allowlist. **Не** читает чужой agent chat по умолчанию.

## Сущности

| Сущность | Описание |
|----------|----------|
| `Company` | Организация (создаёт Platform Admin) |
| `Employee` | Person + `keycloak_sub` + membership |
| `EmployeeCabinetAssignment` | M:N ⊆ `CompanyCabinetGrant` |
| `EmployeeStatus` | `invited` \| `active` \| `disabled` |
| `CompanyAgentPolicy` | `preferred_provider`, `platform_fallback` |

## Операции

| Операция | Инвариант |
|----------|-----------|
| `employee.invite` | Email → Keycloak invite + DB stub `invited`. **Без password в Prodavan API** |
| `employee.disable` / `enable` | Disabled → 403 на cabinet/project API |
| `employee.assign_cabinets` | Только ∩ CompanyCabinetGrant; enforced на enter/create |
| `metrics.employees` | Read aggregates |

## Наследование кабинетов

```text
Admin grants profile_ids → Company
Company assigns cabinet instances / profiles → Employee
Employee login sees only assigned
create_cabinet / enter → must pass grant check
```
