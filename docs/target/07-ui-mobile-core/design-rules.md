# Design rules

Канон построения: [principles.md](principles.md) (reuse, decomposition, EntityCollection, laconic, responsive, buttons).

## Обязательно

1. **Mobile-first:** layouts для телефона; web — тот же chrome. Адаптация — [responsive.md](responsive.md).
2. **Material 3** через `AppTheme` / tokens.
3. Выбор сущностей и confirm — **только pages** (`Navigator.push`).
4. Коллекции сущностей — [EntityCollection](entity-collection.md) (list ↔ table); строки list — `AppListItem`.
5. Виджеты — только `lib/core/`; feature собирает экраны ([composition.md](composition.md)).
6. Лаконичность: нет instructional copy; title только в слоте контейнера ([principles.md](principles.md) §4).
7. Кнопки — [buttons.md](buttons.md) (icon / icon-toggle / короткое существительное).
8. Секции — [app-section-header.md](app-section-header.md): header только для крупных блоков; не дублировать title preference/selector строк.

## Запрещено

| Паттерн | Почему |
|---------|--------|
| `showDialog` / `AlertDialog` | Модалка |
| `showModalBottomSheet` | Модалка |
| `PopupMenuButton` для выбора сущностей | Не page-selector |
| `DropdownButton` для сущностей/enum с >2 значимыми опциями | Замена: `AppSelectorPage` / `AppChoicePreference` |
| `AppTextField` / `AppPasswordField` на **settings/detail** (seamless) | Замена: [preferences.md](preferences.md); на create/submit-формах — как раз `AppTextField` / `AppPasswordField` + `AppButton` |
| Сырой `TextField` / `FilledButton` / `IconButton` в feature | Только core: `AppTextField`, `AppPasswordField`, `AppButton`, `AppIconButton` |
| Локальные ListTile / DataTable «на один экран» | Дублирование EntityCollection / `AppListItem` |
| Свободные заголовки/подсказки «как пользоваться» | Нарушение laconic |
| Feature-local кнопки в обход core | Нарушение reuse |

Допустимы: системный file picker OS; `AppSnackBar` для лёгкого feedback (не confirm).
Inline status — `AppStatusBanner` с `AppStatusSeverity` (info / success / warning / error / critical), цвета только из `AppColorTokens`.

## Навигация

- `AppScaffold` + optional `NavigationBar` / rail через `AppLayout`.
- Деструктивные действия → `AppConfirmPage` (full screen, `AppStatusSeverity.error`).
