# Platform Admin — UX contract

Эталон: **Stripe Dashboard** + **Linear**.  
Коллекции: [EntityCollection](../07-ui-mobile-core/entity-collection.md). Кабинеты: [dynamic](../05-cabinets/dynamic-cabinets.md).

## Семантика UI

Control plane платформы. Нет chat проектов, нет доменных cabinet screens.

## IA (bottom NavigationBar)

| Tab | Экран | Содержимое |
|-----|-------|------------|
| Сводка | `AdminOverviewPage` | Alerts first, затем StatTiles |
| Компании | `AdminCompaniesPage` | EntityCollection companies |
| Ключи ИИ | `AdminAiKeysPage` | Keys → detail / create |
| Bundles | `AdminStarterBundlesPage` | Optional starter cabinet bundles |
| Профиль | `AdminProfilePage` | Аккаунт admin |

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
