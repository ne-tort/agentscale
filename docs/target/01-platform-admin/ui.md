# Platform Admin — UI

Все экраны — **full-screen pages**. Выбор — `AppSelectorPage`. Confirm — `DangerConfirmPage`.

Профессиональный контракт: **[ux-contract.md](ux-contract.md)**.

## Навигация

### Bottom NavigationBar (narrow)

| Tab | Экран | Назначение |
|-----|-------|------------|
| Обзор | `AdminMetricsOverviewPage` | Alerts → метрики |
| **Управление** | `AdminManagementPage` | Хаб → Компании / AI-ключи / Контейнеры / Кабинеты |
| Настройки | Settings chrome | Аккаунт admin |

### Rail (medium / expanded)

| Destination | Экран | Назначение |
|-------------|-------|------------|
| Обзор | `AdminMetricsOverviewPage` | Alerts → метрики |
| Компании | `AdminCompanyListPage` | Список → detail |
| AI-ключи | `AdminAiKeyListPage` | Список → detail / create |
| Контейнеры | `AdminProjectContainersPage` | Runtime isolators ([14](../14-project-containers/admin-ui.md)) |
| Кабинеты | stub | Placeholder («скоро») |
| Настройки | trailing | Аккаунт admin |

**Deprecate:** tab «Бандлы» / `AdminStarterBundlesPage` в admin shell. Starter `cabinet.bundle` catalog — employee import / API, не admin chrome ([05 bundle-format](../05-cabinets/bundle-format.md)).

## Потоки (кратко)

- Создать компанию: inline name → detail; invite company.admin и квоты — seamless на detail (**без password**, без batch Save).
- AI keys: inline name → detail; preference kit.
- AI key bindings / agent policy: selectors на company detail.
- Контейнеры: sort running-first; Pause/Resume/Delete через Project; Force kill — ContainerPort ([14](../14-project-containers/)).
- Опасные действия: `DangerConfirmPage`.
