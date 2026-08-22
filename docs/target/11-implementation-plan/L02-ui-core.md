# L02 — UI mobile core

## Цель

Полный набор **core** примитивов и UX-системы по канону 07. Без feature-домена. Все последующие контуры только собирают экраны из core.

## Канон

- [07-ui-mobile-core/](../07-ui-mobile-core/) целиком: [principles](../07-ui-mobile-core/principles.md), [entity-collection](../07-ui-mobile-core/entity-collection.md), [app-selector-page](../07-ui-mobile-core/app-selector-page.md), [buttons](../07-ui-mobile-core/buttons.md), [responsive](../07-ui-mobile-core/responsive.md), [ux-system](../07-ui-mobile-core/ux-system.md), [design-rules](../07-ui-mobile-core/design-rules.md), …

## Зависимости

| Нужно | Даёт |
|-------|------|
| L00 Flutter layout | Виджеты + токены + навигационные паттерны для L04–L07 |

## Контракты (публикует)

| Контракт | Описание |
|----------|----------|
| `AppEntityCollection` | list/table + toolbar slots |
| `AppSelectorPage` | multi/single select full-page |
| `AppListItem` / `AppCheckbox` / `AppRadio` | единственные контролы выбора |
| `AppIconButton` / button taxonomy | [buttons.md](../07-ui-mobile-core/buttons.md) |
| `AppBreakpoints` | phone/tablet/wide behavior |
| `DangerConfirmPage` | confirm без AlertDialog |
| Lint/rule (желательно) | запрет `showDialog` / `AlertDialog` в `lib/features` |

## Изоляция

**Максимальная.** Делать **весь** surface 07 до появления API. Widgetbook / golden / widget tests.

## DoD

- [x] Реализованы все принципы §1–6 из principles.md (reuse, decomposition, EntityCollection, laconic, responsive, buttons).
- [x] EntityCollection: list↔table по breakpoint; один `onOpen`.
- [x] Selector page: search, multiSelect, checkboxes из core.
- [x] **Ноль** модалок/dropdown для выбора сущностей в core и в demo gallery.
- [x] Spacing/theme tokens по канону; не default Inter-only «временная» тема вразрез design-rules (если правила заданы).
- [x] Gallery/demo route: все примитивы видимы без backend.
- [x] Автотесты на ключевые виджеты + (опционально) screenshot golden.

## Не считать готовым, если…

- «Пока Dialog, потом заменим».
- Feature-экран завёл свой ListTile/кнопки в обход core.
- Только ThemeData без EntityCollection/Selector.
- Core считается done, а checklist principles не пройден по пунктам.

## Exit gate

UI gallery зелёная; code review checklist 07; готовность L04/L05 собирать shells без новых примитивов.
