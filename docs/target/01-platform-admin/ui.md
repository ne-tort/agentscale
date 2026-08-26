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

- Создать компанию: inline name → detail; invite company.admin и квоты — seamless на detail (**без password**, без batch Save).
- AI keys: inline name → detail; preference kit.
- AI key bindings / agent policy: selectors на company detail.
- Опасные действия: `DangerConfirmPage`.
