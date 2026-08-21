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

## Правила для составных виджетов

- `AppListItem` horizontal padding = `space.lg`, vertical = `space.md`.
- `AppSelectorPage` list = те же paddings, что `AppListItem`.
- Checkbox/radio hit target ≥ 48×48; визуальный box не обязан быть 48, но tap area — да.
- Не добавлять «магические» 6/10/14 px вне шкалы.
