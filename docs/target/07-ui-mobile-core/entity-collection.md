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
| Admin | Companies, AI keys, cabinet catalog |
| Company | Employees, assigned cabinets / grants |
| Employee | Projects; cabinet domain: prompts, runs, catalogs, … |

## Слоты оболочки (декомпозиция)

```text
AppEntityCollection
  ├── toolbar?     # AppIconButton: add, search, filter; без простыней текста
  ├── body         # list | table
  ├── empty        # EmptyState: факт + короткое noun-label действия
  └── loading      # skeleton rows (не обязательный full-screen spinner)
```

Feature передаёт: columns/fields, row builder / cell values, `onOpen`, toolbar actions.  
Feature **не** копирует ListView/DataTable стили.

## Строка / колонки

- Title = главный идентификатор сущности.
- Secondary cells / subtitle = **данные** (status, dates, counts) — не пояснения.
- Leading/trailing — из core (icon, chevron, status tone).
- Tap → detail/form **page**, не modal.

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
