# Employee UI — продуктовый канон

Изолированная документация контура **сотрудника** (employee). Не путать с company admin (`features/company/`) и platform admin (`features/admin/`).

См. также: [PRODUCT.md](../PRODUCT.md).

## Модули

| # | Документ | Тема |
|---|----------|------|
| 01 | [01-entry-flow.md](01-entry-flow.md) | Login → cabinet picker → auto-enter |
| 02 | [02-cabinet-shell.md](02-cabinet-shell.md) | `CabinetShell`, rail, breakpoints |
| 03 | [03-cabinet-overview.md](03-cabinet-overview.md) | Обзор кабинета, метрики |
| 04 | [04-projects.md](04-projects.md) | Таблица проектов, inline add, delete |
| 05 | [05-project-settings.md](05-project-settings.md) | Настройки проекта |
| 06 | [06-settings.md](06-settings.md) | Password, email, switch cabinet |
| 07 | [07-ai-keys-scope.md](07-ai-keys-scope.md) | Привязка ключей employee/cabinet |
| 08 | [08-meta-navigation.md](08-meta-navigation.md) | Meta tabs → nav |
| 09 | [09-legacy-deletion.md](09-legacy-deletion.md) | Что удалено |
| 10 | [10-backend-gaps.md](10-backend-gaps.md) | API gaps и статус |

## Глоссарий

- **Cabinet** — рабочее пространство сотрудника (`cabinet_instances`), привязка через assignment.
- **CabinetShell** — главный UI после выбора кабинета (аналог `CompanyShell`).
- **Project** — изолированный Pod + workspace; создаётся внутри кабинета.
- **Module** — meta-документы (tables/views/tabs) → UI через interpreters.

## Паттерны UI (как Admin/Company)

- Shell: `AppLayout` + `NavigationRail` + logo → overview + settings trailing
- Таблицы: `AppInlineAddField` + `Expanded(AppEntityCollection)`
- Delete: long-press → иконка в последней колонке + `AppConfirmPage`
- Settings: `SettingsPage(embedded: true, leadingChildren: [...])`

## Код

```
apps/flutter/lib/features/employee/
  auth/                    → re-export or use features/auth/
  cabinet_picker_page.dart
  cabinet_shell.dart
  cabinet_overview_page.dart
  cabinet_projects_page.dart
  cabinet_project_settings_page.dart
  employee_settings_body.dart
  cabinet_management_page.dart   # narrow hub
  cabinet_nav_loader.dart
  cabinet_module_host.dart
```
