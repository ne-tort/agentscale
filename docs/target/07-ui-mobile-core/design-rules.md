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

## Запрещено

| Паттерн | Почему |
|---------|--------|
| `showDialog` / `AlertDialog` | Модалка |
| `showModalBottomSheet` | Модалка |
| `PopupMenuButton` для выбора сущностей | Не page-selector |
| `DropdownButton` для сущностей/enum с >2 значимыми опциями | Замена: `AppSelectorPage` |
| Локальные ListTile / DataTable «на один экран» | Дублирование EntityCollection / `AppListItem` |
| Свободные заголовки/подсказки «как пользоваться» | Нарушение laconic |
| Feature-local кнопки в обход core | Нарушение reuse |

Допустимы: системный file picker OS; SnackBar для лёгкого feedback (не confirm).

## Навигация

- `AppScaffold` + optional `NavigationBar` / rail через `AppLayout`.
- Деструктивные действия → `DangerConfirmPage` (full screen).
