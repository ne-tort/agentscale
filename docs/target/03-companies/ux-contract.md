# Companies — UX contract

Эталон: **Notion Members** + org metrics.  
Кабинеты: [dynamic](../05-cabinets/dynamic-cabinets.md). EntityCollection + laconic UI.

## IA (bottom NavigationBar)

| Tab | Экран |
|-----|-------|
| Сводка | `CompanyOverviewPage` — сотрудники, подписка, usage |
| Сотрудники | `CompanyEmployeesPage` → detail |
| Кабинеты | `CompanyCabinetsPage` — EntityCollection instances компании (owner, name, counts) read-mostly |
| Профиль | `CompanyProfilePage` |

Не открывать Employee dynamic shell как основной путь Company; явный «Кабинеты» только для org overview (или switch contour если тот же человек — employee).

## Потоки

### Invite

Form: email, display name. **Нет** multi-select статических cabinet grants.  
CTA: «Создать» / «Сохранить».

### Enable / disable

Full pages / `DangerConfirmPage`.

### Кабинеты (org)

List: name, owner employee, updated — tap → read-only summary (counts, not rows).  
Нет редактирования чужих tables без break-glass policy.

### Метрики

Projects, tokens, last activity — drill-down read-only. Chat сотрудника — default off.

## Density / feedback

Dense lists; Snack soft; InlineErrorBanner blocking.
