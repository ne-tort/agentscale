# Companies — UI

Контур отделён от Admin и Employee.

Полный UX-контракт: **[ux-contract.md](ux-contract.md)**.

## NavigationBar

| Tab | Экран |
|-----|-------|
| Сводка | `CompanyOverviewPage` |
| Сотрудники | `CompanyEmployeesPage` → detail |
| Кабинеты | `CompanyCabinetsPage` (org list / metrics) |
| Профиль | `CompanyProfilePage` |

## Потоки (кратко)

- Invite employee: email + display name; **нет password**; **нет** static cabinet grants multi-select.
- Enable/disable: status / danger page.
- Cabinets: read-mostly org overview (owner, name) — не чужие rows.
- Metrics: read-only; без входа в чужой чат по умолчанию.
