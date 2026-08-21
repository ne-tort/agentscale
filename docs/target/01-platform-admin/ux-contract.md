# Platform Admin — UX contract

Эталон плотности: **Stripe Dashboard** (ops) + **Linear** (lists). Не «школьный CRUD».  
Коллекции: [EntityCollection](../07-ui-mobile-core/entity-collection.md). Лаконичность: [principles](../07-ui-mobile-core/principles.md) §4.

## Семантика UI

Control plane всей платформы. Нет чата проектов, нет домена закупок.

## IA (bottom NavigationBar)

| Tab | Экран | Содержимое |
|-----|-------|------------|
| Сводка | `AdminOverviewPage` | **Сначала alerts**, затем StatTiles; tap → drill-down page |
| Компании | `AdminCompaniesPage` | `AppEntityCollection` (companies) + search/filter icons |
| Ключи ИИ | `AdminAiKeysPage` | EntityCollection → detail / create |
| Кабинеты | `AdminCabinetCatalogPage` | EntityCollection: modules + who has grant |
| Профиль | `AdminProfilePage` | Аккаунт admin |

## Chrome

- Title только в слоте `AppScaffold` / section; нет free-floating подсказок.
- Коллекции: EntityCollection (list/table); строки list — `AppListItem` (dense).
- EmptyState: короткий факт + «Создать» (noun), без обучающих абзацев.
- Loading: list skeletons предпочтительнее full-screen spinner на повторных заходах.
- Toolbar: `AppIconButton` / toggle ([buttons](../07-ui-mobile-core/buttons.md)).

## Потоки

### Создать компанию

`AdminCompanyFormPage`: name, slug, subscription (lifetime toggle **или** дата через selector).  
Invite first `company.admin`: **email only** → Keycloak (нет поля password).  
Начальные grants: multi `AppSelectorPage`.

### Grants / keys

Company detail sections → multi selector pages → save.  
Danger (suspend/delete): `DangerConfirmPage`.

### AI key

Create page (secret once) → renew months via `AppSelectorPage` → bind companies multi-select.

## Definition of done (телефон)

Admin создаёт компанию → ключ → grant `equipment-procurement` → видит alert на сводке — без модалок.
