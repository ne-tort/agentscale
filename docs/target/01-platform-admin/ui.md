# Platform Admin — UI

Все экраны — **full-screen pages**. Выбор — `AppSelectorPage`. Confirm — `DangerConfirmPage`.

Профессиональный контракт: **[ux-contract.md](ux-contract.md)**.

## Навигация (bottom NavigationBar)

| Tab | Экран | Назначение |
|-----|-------|------------|
| Сводка | `AdminOverviewPage` | Alerts → компании, ключи, подписки |
| Компании | `AdminCompaniesPage` | Список → detail (квоты, keys, policy) |
| Ключи ИИ | `AdminAiKeysPage` | Список → detail / create |
| Bundles | `AdminStarterBundlesPage` | Optional starter cabinet bundles |
| Профиль | `AdminProfilePage` | Аккаунт admin |

## Потоки (кратко)

- Создать компанию: form + email invite company.admin (**без password**) + cabinet quotas.
- AI key bindings / agent policy: selectors на company detail.
- Опасные действия: `DangerConfirmPage`.
