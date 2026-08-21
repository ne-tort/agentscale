# AppRadio

Унифицированный radio для single-select (формы и списки).

## Виды (`variant`)

| variant | Визуал |
|---------|--------|
| `material` | Классический круг |
| `chip` | Выбор как chip (selected fill) |
| `check_mark` | Не круг: галочка в квадрате (radio semantics) |

## Опции

| Опция | Тип | Описание |
|-------|-----|----------|
| `value` | T | Значение этой опции |
| `groupValue` | T? | Текущий выбор группы |
| `onChanged` | `ValueChanged<T?>?` | |
| `label` | String? | |
| `variant` | enum | |
| `enabled` | bool | |

## Синтаксис

```dart
AppRadio<int>(
  value: 3,
  groupValue: months,
  label: '3 месяца',
  variant: AppRadioVariant.chip,
  onChanged: (v) => setState(() => months = v),
)
```

Для списка месяцев продления ключа (1–12) — либо колонка `AppRadio`, либо `AppSelectorPage` с `showRadios: true` (предпочтительно для длинных списков).
