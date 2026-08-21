# Responsive — централизованная адаптация

Приложение строится с расчётом на телефон и web. Адаптация **одна на продукт**, не per-feature.

## Семантика

| Компонент (канон) | Роль |
|-------------------|------|
| `AppBreakpoints` | Единые пороги ширины (значения px — гибко; наличие — строго) |
| `AppLayout` | Слоты: `body`, optional `nav` (bottom / rail), optional `aside` |
| EntityCollection layout | `list` ниже breakpoint, `table` выше |

Features потребляют **слоты и режимы**, не сырой `MediaQuery` для своей сетки колонок.

## Поведение

| Ширина | Chrome | Коллекции |
|--------|--------|-----------|
| narrow (phone) | `NavigationBar` bottom | list (`AppListItem`) |
| wide | тот же shell; nav может стать rail **только** через AppLayout | table |

Web на desktop ≠ отдельный «desktop design»; это тот же mobile-first chrome + table density.

## Запрещено в features

```dart
// антипаттерн
if (MediaQuery.sizeOf(context).width > 800) { /* своя вёрстка таблицы */ }
```

Допустимо: читать `AppBreakpoints.of(context)` / `AppLayout.isWide`.

## Гибко

- Конкретные px (`compact` / `medium` / `expanded`) можно менять в одном месте.
- Число колонок table — per EntityCollection config.

## Связь

- [principles.md](principles.md) §5  
- [entity-collection.md](entity-collection.md)  
- [design-rules.md](design-rules.md) mobile-first
