# Companies — UI

Контур отделён от Admin и Employee.

Полный UX-контракт: **[ux-contract.md](ux-contract.md)**.

## NavigationBar

| Tab | Экран |
|-----|-------|
| Сводка | `CompanyOverviewPage` |
| Сотрудники | `CompanyEmployeesPage` → detail |
| Кабинеты | `CompanyCabinetsPage` |
| Профиль | `CompanyProfilePage` |

## Потоки (кратко)

- Invite employee: email + cabinets multi-select; **нет password**.
- Enable/disable: status / danger page.
- Metrics: read-only; без входа в чужой чат по умолчанию.
