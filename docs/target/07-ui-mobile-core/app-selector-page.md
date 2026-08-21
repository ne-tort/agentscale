# AppSelectorPage

Полноэкранная страница выбора. Заменяет dropdown, popup menu и modal pickers.

## Возможности (включаются опционально)

| Опция | Тип | Описание |
|-------|-----|----------|
| `title` | String | AppBar title |
| `items` | `List<AppSelectorItem>` | Данные |
| `multiSelect` | bool | Множественный выбор |
| `selectedIds` | `Set<String>` | Начальный выбор |
| `searchEnabled` | bool | Поле поиска сверху |
| `showCheckboxes` | bool | Явные checkbox (обычно с multi) |
| `showRadios` | bool | Radio для single |
| `itemBuilder` | optional | Кастом, по умолчанию `AppListItem` |
| `warningBanner` | String? | Плашка над списком |
| `empty` | Widget? | EmptyState |
| `onConfirm` | `ValueChanged<Set<String>>` | Кнопка «Готово» в AppBar (multi) |
| `popOnSelect` | bool | Single: выбрать и pop сразу |

## `AppSelectorItem`

| Поле | Описание |
|------|----------|
| `id` | Стабильный id |
| `title` | |
| `subtitle` | |
| `leading` | IconData / Widget |
| `trailing` | Widget? |
| `tone` | как у list item |
| `enabled` | |

## Синтаксис

```dart
final ids = await Navigator.of(context).push<Set<String>>(
  MaterialPageRoute(
    builder: (_) => AppSelectorPage(
      title: 'Кабинеты',
      multiSelect: true,
      showCheckboxes: true,
      searchEnabled: true,
      selectedIds: current,
      items: grants.map((g) => AppSelectorItem(
        id: g.profileId,
        title: g.displayName,
        subtitle: g.profileId,
        leading: Icon(Icons.dashboard_outlined),
      )).toList(),
    ),
  ),
);
```

## Инварианты

- Внутри только `AppListItem` (+ search field / banner).
- Нет `DropdownButton` внутри страницы для тех же items.
