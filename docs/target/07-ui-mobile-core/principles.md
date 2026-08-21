# UI principles (канон построения)

Шесть жёстких принципов. Нарушение в feature-коде — дефект относительно канона.  
Детали паттернов: [entity-collection](entity-collection.md) · [composition](composition.md) · [buttons](buttons.md) · [responsive](responsive.md).

---

## 1. Reuse — только core

- Управляющие виджеты живут в `apps/flutter/lib/core/…`.
- Feature-экраны **собирают** экраны из core; не заводят локальные кнопки / list / table «на один экран».
- Новый визуальный контрол → сначала в core, потом в feature.

## 2. Decomposition — не монолиты

- Составной виджет = слоты из мелких переиспользуемых частей (`AppListItem`, `AppIconButton`, `AppEntityCollection`, …).
- Если фрагмент можно вынести и переиспользовать (≥2 места или очевидно общий) — вынести в core.
- Private `_Helper` внутри core-файла допустим; в features — нет.

См. [composition.md](composition.md).

## 3. Primary surface — EntityCollection

Основной способ работы с сущностями — **одна** унифицированная коллекция (list на узком / table на широком):

| Contour | Примеры коллекций |
|---------|-------------------|
| Admin | Companies, AI keys, cabinet catalog |
| Company | Employees, grants |
| Employee | Projects; prompts, runs, … |

Знакомый UX при смене сущности: проекты → промпты = тот же каркас, другие колонки/данные.

См. [entity-collection.md](entity-collection.md).

**Исключение:** agent chat workspace — не таблица; атомы (icon buttons, tool rows) всё равно из core.

## 4. Лаконичность

- **Нет** instructional copy, подсказок «как пользоваться», обучающих абзацев в UI.
- **Нет** свободных заголовков/подзаголовков «текстом в пустоте».
- Title / section title — **только** слот контейнера (`AppScaffold.title`, header slot секции) по правилам контейнера.
- `AppListItem.subtitle` — только **данные** (status, id, meta), не инструкция.
- EmptyState: короткий факт + label действия («Создать»), без «создайте проект чтобы…».
- Справки — будущий унифицированный `HelpSystem`, не смешивать с chrome.

## 5. Mobile — централизованная адаптация

- Breakpoints / layout slots только через core (`AppBreakpoints` / `AppLayout`).
- Features **не** пишут ad-hoc `MediaQuery` для своей сетки.
- Phone: list + bottom nav; wide: table mode + тот же chrome.

См. [responsive.md](responsive.md).

## 6. Buttons

| Вид | Когда |
|-----|-------|
| Icon | По умолчанию (toolbar / chrome) |
| Icon-toggle | Режимы (цвет/selected меняется) |
| Labeled | Primary / неочевидная семантика; label = **короткое существительное** («Экспорт», «Создать») |

См. [buttons.md](buttons.md).

---

## Строго / гибко

| Тема | Строго | Гибко |
|------|--------|-------|
| Место виджетов | Только core | Состав экрана в feature |
| Коллекции | Один EntityCollection pattern | Колонки / leading per entity |
| Модалки / popup entity pick | Запрет | — |
| Instructional copy | Запрет | Текст в будущем Help |
| Заголовки | Только слот контейнера | Формулировка 1–3 слова |
| Breakpoints | Централизованы | Конкретные px |
| Кнопки | Core + правила | Icon vs label по семантике |
| Chat workspace | Не table; атомы из core | Состав bubbles/composer |

## Связь с запретом модалок

Выбор сущностей и confirm — pages (`AppSelectorPage`, `DangerConfirmPage`). См. [design-rules.md](design-rules.md).
