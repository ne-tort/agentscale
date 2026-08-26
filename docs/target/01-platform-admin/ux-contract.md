# Platform Admin — UX contract

Эталон: **Stripe Dashboard** + **Linear**.  
Коллекции: [EntityCollection](../07-ui-mobile-core/entity-collection.md). Кабинеты: [dynamic](../05-cabinets/dynamic-cabinets.md).

## Семантика UI

Control plane платформы. Нет chat проектов, нет доменных cabinet screens.

## IA (bottom NavigationBar) — narrow

На телефоне секции управления сгруппированы, чтобы bottom bar не переполнялся.

| Tab | Экран | Содержимое |
|-----|-------|------------|
| Обзор | `AdminMetricsOverviewPage` | Alerts first, затем StatTiles |
| **Управление** | `AdminManagementPage` | Список: Компании → AI-ключи → Контейнеры → Кабинеты (push full-screen) |
| Настройки | `Settings` (chrome) | Аккаунт / prefs |

### IA (rail) — medium / expanded

| Destination | Экран |
|-------------|-------|
| Обзор | `AdminMetricsOverviewPage` |
| Компании | `AdminCompanyListPage` |
| AI-ключи | `AdminAiKeyListPage` |
| Контейнеры | `AdminProjectContainersPage` |
| Кабинеты | stub |
| Настройки | trailing / bottom Settings |

**Deprecate:** tab Bundles / `AdminStarterBundlesPage` в admin chrome.

(Agent policy / quotas — sections в company detail или отдельный tab «Политики».)

## Chrome

- Titles только в слотах контейнера; laconic EmptyPlaceholder.
- EntityCollection; icon toolbar ([buttons](../07-ui-mobile-core/buttons.md)).

## Потоки

### Создать компанию

Inline `AppInlineAddField` «Добавить компанию» (только name) → detail page.  
Invite `company.admin`: preference на general (email only → Keycloak).  
Cabinet quotas / subscription — seamless preferences на detail sub-pages.

### Keys / policy

Company detail → bind AI keys; set tool preset / model allowlist / quotas.  
Danger: `DangerConfirmPage`.

## Definition of done (телефон)

Admin создаёт компанию → ключ → квоты кабинетов → видит alert на сводке — без модалок.
