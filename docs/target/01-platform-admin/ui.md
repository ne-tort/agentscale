# Platform Admin — UI

Все экраны — **full-screen pages**. Выбор — `AppSelectorPage`. Confirm — `DangerConfirmPage`.

Профессиональный контракт (плотность, alerts, flows): **[ux-contract.md](ux-contract.md)**.

## Навигация (bottom NavigationBar)

| Tab | Экран | Назначение |
|-----|-------|------------|
| Сводка | `AdminOverviewPage` | Alerts → компании, ключи, подписки |
| Компании | `AdminCompaniesPage` | Список → detail |
| Ключи ИИ | `AdminAiKeysPage` | Список → detail / create |
| Кабинеты | `AdminCabinetCatalogPage` | Catalog + grants |
| Профиль | `AdminProfilePage` | Аккаунт admin |

## Потоки (кратко)

- Создать компанию: form page + email invite company.admin (**без password**) + grants selector.
- Grants / AI bindings: multi `AppSelectorPage`.
- Опасные действия: `DangerConfirmPage`.
