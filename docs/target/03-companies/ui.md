# Companies — UI

Контур **отделён** от Admin и Employee, по IA **зеркалит Admin** (локальный scope).

Полный UX: **[ux-contract.md](ux-contract.md)**. Домен: **[domain.md](domain.md)**.

## NavigationBar

| Tab | Экран |
|-----|-------|
| Сводка | `CompanyOverviewPage` |
| Сотрудники | `CompanyEmployeesPage` → detail |
| Контейнеры | `CompanyContainersPage` → detail |
| Ключи ИИ | `CompanyAiKeyListPage` → detail |
| Кабинеты | `CompanyCabinetsPage` (RO MVP) |
| Профиль | `CompanyProfilePage` |

## Потоки (кратко)

- Invite / enable / disable employee.
- Assign cabinet ↔ employee.
- Oversight containers сотрудников (pause/resume/delete).
- AI keys: create local (SDK/API); see Admin-linked RO.
- Cabinets: view Admin-assigned RO; local create — future.
