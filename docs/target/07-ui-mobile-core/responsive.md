# Responsive — централизованная адаптация

Приложение строится с расчётом на телефон и web. Адаптация **одна на продукт**, не per-feature.

## Семантика

| Компонент (канон) | Роль |
|-------------------|------|
| `AppBreakpoints` | Пороги: `narrowMax` 600, `mediumMax` 1024, `contentMaxWidth` 840 |
| `AppLayout` | Adaptive nav chrome: bottom bar / **left** rail; body column |
| `AppScaffold` | AppBar **и** body в одной колонке ≤ `contentMaxWidth` (кроме `expandBody: true`) |
| EntityCollection layout | `list` ниже breakpoint, `table` выше |

Features потребляют **слоты и режимы**, не сырой `MediaQuery` для своей сетки колонок.

## Поведение chrome (`AppLayout`)

| Ширина | Nav | Контент |
|--------|-----|---------|
| narrow (`< 600`) | `NavigationBar` снизу | колонка ≤ `contentMaxWidth` (AppBar+body), по центру |
| medium (`600–1024`) | **левый** `NavigationRail`, icon над label (`labelType: all`) | то же; rail **вне** max-width |
| expanded (`≥ 1024`) | **левый** extended rail (icon + label в одну линию) | то же |

Web на desktop ≠ отдельный «desktop design»; тот же chrome + table density на wide.

## Запрещено в features

```dart
// антипаттерн
if (MediaQuery.sizeOf(context).width > 800) { /* своя вёрстка таблицы */ }
```

Допустимо: `AppBreakpoints.isNarrow/isMedium/isExpanded(context)`.

## Гибко

- Конкретные px меняются только в `AppBreakpoints`.
- Число колонок table — per EntityCollection config.

## Связь

- [principles.md](principles.md) §5  
- [entity-collection.md](entity-collection.md)  
- [design-rules.md](design-rules.md) mobile-first
