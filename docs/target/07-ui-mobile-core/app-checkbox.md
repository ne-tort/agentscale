# AppCheckbox

Унифицированный checkbox для форм и list selection.

## Виды (`variant`)

| variant | Визуал |
|---------|--------|
| `material` | Стандартный M3 checkbox |
| `filled_square` | Залитый квадрат |
| `tonal` | Tonal container |

Все варианты — один виджет, не три файла.

## Опции

| Опция | Тип | Описание |
|-------|-----|----------|
| `value` | bool | |
| `onChanged` | `ValueChanged<bool?>?` | null = disabled |
| `label` | String? | Текст справа |
| `variant` | enum | См. выше |
| `tristate` | bool | |
| `semanticLabel` | String? | |

## Синтаксис

```dart
AppCheckbox(
  value: selected,
  label: 'Привязать к компании',
  variant: AppCheckboxVariant.material,
  onChanged: (v) => setState(() => selected = v ?? false),
)
```

В `AppListItem` / selector передаётся через `selectionControl: AppCheckbox(...)`.
