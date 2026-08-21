# Companies — UX contract

Эталон: **Notion Members** + grants. Отдельный shell от Admin и Employee.  
Коллекции: [EntityCollection](../07-ui-mobile-core/entity-collection.md). Лаконичность: [principles](../07-ui-mobile-core/principles.md) §4.

## IA (bottom NavigationBar)

| Tab | Экран |
|-----|-------|
| Сводка | `CompanyOverviewPage` — сотрудники, подписка (read-only), usage |
| Сотрудники | `CompanyEmployeesPage` — EntityCollection → detail |
| Кабинеты | `CompanyCabinetsPage` — EntityCollection: grants (read-only) + кто назначен |
| Профиль | `CompanyProfilePage` |

**Не** пушить Employee cabinet shell из Company как «основной» путь; отдельный явный item «Кабинеты» (короткий noun / icon) только если тот же человек — employee.

## Потоки

### Invite сотрудника

Form page: email, display name, cabinets multi-select (⊆ grants).  
Submit → Keycloak invite. **Нет password field.**  
CTA: «Создать» / «Сохранить» — короткое существительное.

### Enable / disable

`CompanyEmployeeStatusPage` / `DangerConfirmPage` — полные страницы.

### Метрики

Detail: проекты, токены, last activity → EntityCollection / `AppListItem` drill-down в **read-only** project summary.  
Вход в чат сотрудника — **запрещён** без отдельной политики (default off).

## Density / feedback

Dense EntityCollection; Snack на soft success; blocking errors — `InlineErrorBanner`.  
EmptyState без instructional copy.
