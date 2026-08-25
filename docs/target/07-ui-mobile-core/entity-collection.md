# EntityCollection — унифицированные списки и таблицы

Основная поверхность управления сущностями во всех контурах.  
Пользователь, переходя с «Проекты» на «Промпты», видит **тот же** каркас.

## Семантика

`AppEntityCollection` (целевое имя в core) = оболочка над одним data model:

| Режим | Когда | Рендер строки |
|-------|-------|---------------|
| `list` | narrow / phone (`AppBreakpoints`) | `AppListItem` |
| `table` | wide | Те же поля как колонки; row tap = тот же `onOpen` |

Переключение list/table — `AppIconToggle` в toolbar коллекции (если оба режима доступны), не два разных экрана.

## Где применяется

| Contour | Коллекции |
|---------|-----------|
| Admin | Companies, AI keys, starter bundles |
| Company | Employees, org cabinets (metrics) |
| Employee | Own cabinets; Projects; dynamic tabs from meta |

## Слоты оболочки (декомпозиция)

```text
AppEntityCollection
  ├── toolbar?     # AppIconButton: add, search, filter; без простыней текста
  ├── body         # list | table
  ├── empty        # EmptyPlaceholder: факт + короткое noun-label действия
  └── loading      # skeleton rows (не обязательный full-screen spinner)
```

Feature передаёт: columns/fields, row builder / cell values, `onOpen`, toolbar actions.  
Feature **не** копирует ListView/DataTable стили.

## Строка / колонки

- Title = главный идентификатор сущности.
- Secondary cells / subtitle = **данные** (status, dates, counts) — не пояснения.
- Leading/trailing — из core (icon, chevron, status tone).
- Tap → detail/form **page**, не modal.

## Table mode — ширина и скролл

Режим `table` в `AppEntityCollection` вычисляет минимальную ширину таблицы:

```text
minTableWidth = horizontalMargin × 2
              + primaryMinWidth (140)
              + Σ column.width (если задан)
              + columnSpacing × N колонок
```

Константы: `_horizontalMargin = 12`, `_columnSpacing = 12`, `_primaryMinWidth = 140`.

| Правило | Поведение |
|---------|-----------|
| `minTableWidth ≤ parentWidth` | Таблица растягивается на доступную ширину (clamp до `AppBreakpoints.contentMaxWidth`) |
| `minTableWidth > parentWidth` | Горизонтальный `SingleChildScrollView` — таблица не сжимается ниже min |
| Primary column | Заголовок из `primaryColumnLabel` (fallback: `commonEntity`); ячейка = `row.title` |
| Fixed columns | `AppEntityColumn.width` задаёт фиксированную ширину ячейки; участвует в `minTableWidth` |
| Flex columns | `flex` зарезервирован; в текущей реализации DataTable использует auto-width для колонок без `width` |

Feature передаёт `primaryColumnLabel` когда первая колонка — не generic «Сущность» (например `commonCompany`, `commonEmail`, `adminKey`).

## Toolbar

- По умолчанию icon buttons (`AppIconButton`).
- Primary create — icon «add» **или** labeled «Создать» (noun), если без подписи неочевидно.
- Нет абзацев над таблицей «здесь вы можете…».

## Empty / laconic

| OK | Запрещено |
|----|-----------|
| Title: «Нет проектов» + button «Создать» | «Создайте проект, чтобы загрузить спеку и начать…» |
| Title: «Пусто» + «Создать» | Длинный subtitle с инструкцией |

## Связь

- [principles.md](principles.md) §3  
- [responsive.md](responsive.md) — когда list vs table  
- [app-list-item.md](app-list-item.md) — атом строки в list-режиме  
- [buttons.md](buttons.md)
