# Platform Admin — UI

Все экраны — **full-screen pages**. Выбор — `AppSelectorPage`. Confirm — `DangerConfirmPage`.

Профессиональный контракт: **[ux-contract.md](ux-contract.md)**.

## Навигация (bottom NavigationBar)

| Tab | Экран | Назначение |
|-----|-------|------------|
| Сводка | `AdminOverviewPage` | Alerts → компании, ключи, подписки |
| Компании | `AdminCompaniesPage` | Список → detail (квоты, keys, policy) |
| Ключи ИИ | `AdminAiKeysPage` | Список → detail / create |
| **Контейнеры** | `AdminProjectContainersPage` (P1+) | Runtime isolators: list/detail; actions → Project cascade. Канон: [14 admin-ui](../14-project-containers/admin-ui.md) |
| **Кабинеты** | stub page | Placeholder («скоро»); не starter-bundle catalog |
| Профиль | `AdminProfilePage` | Аккаунт admin |

**Deprecate:** tab «Бандлы» / `AdminStarterBundlesPage` в admin shell. Starter `cabinet.bundle` catalog — employee import / API, не admin chrome ([05 bundle-format](../05-cabinets/bundle-format.md)).

## Потоки (кратко)

- Создать компанию: inline name → detail; invite company.admin и квоты — seamless на detail (**без password**, без batch Save).
- AI keys: inline name → detail; preference kit.
- AI key bindings / agent policy: selectors на company detail.
- Контейнеры: sort running-first; Pause/Resume/Delete через Project; Force kill — ContainerPort ([14](../14-project-containers/)).
- Опасные действия: `DangerConfirmPage`.
