# Spacing tokens

Единая шкала (согласована с `AppSpacing` в коде: xs→xl). Составные виджеты используют **только** эти значения для внутренних padding/gap.

| Token | px | Применение |
|-------|-----|------------|
| `space.xs` | 4 | Плотные иконки |
| `space.sm` | 8 | Gap между icon и title |
| `space.md` | 12 | Внутренний padding compact item |
| `space.lg` | 16 | Стандартный padding list item / page horizontal |
| `space.xl` | 24 | Секции |
| `space.xxl` | 32 | Пустые состояния |

Named insets (код `AppInsets`, поверх шкалы):

| Token | значение | Применение |
|-------|----------|------------|
| `trailingActionRight` | `AppSpacing.sm` (8) | Правый отступ trailing chrome: ListTile / preference / AppListItem / inline `+` / radio |
| `trailingIconExtent` | 40 | Hit-target ширины compact IconButton |
| `appBarActionsRight` | `= trailingActionRight` (8) | AppBar `actionsPadding` — тот же правый край, что у list trailing |

Если родитель уже даёт горизонтальный padding — компенсировать (`trailingActionRight − parentPad`), иначе визуальный край уезжает.

## Правила для составных виджетов

- `AppListItem`: left = `sm`/`md`, right = `trailingActionRight` (не symmetric).
- `AppEntityCollection` list: только vertical padding у ListView — горизонталь из item.
- Checkbox/radio: `MaterialTapTargetSize.shrinkWrap` + compact density; внешний правый край = константа.
- Не добавлять «магические» 6/10/14 px вне шкалы.
