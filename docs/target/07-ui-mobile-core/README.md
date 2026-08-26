# 07 — UI mobile core

Mobile-first Material 3. Единые виджеты в `apps/flutter/lib/core/…`. Запрет модалок.  
Канон построения: [principles.md](principles.md).

| Документ | Содержание |
|----------|------------|
| [principles.md](principles.md) | Шесть принципов + строго/гибко |
| [entity-collection.md](entity-collection.md) | Унифицированные list/table коллекции |
| [composition.md](composition.md) | Декомпозиция, запрет монолитов |
| [buttons.md](buttons.md) | Icon / toggle / labeled nouns |
| [responsive.md](responsive.md) | Breakpoints и layout slots |
| [design-rules.md](design-rules.md) | Правила и запреты |
| [ux-system.md](ux-system.md) | Профессиональный UX (3 shells, chat, density) |
| [spacing-tokens.md](spacing-tokens.md) | Общие отступы |
| [app-list-item.md](app-list-item.md) | Атом строки списка |
| [app-selector-page.md](app-selector-page.md) | Страница выбора |
| [app-checkbox.md](app-checkbox.md) | Чекбоксы |
| [app-radio.md](app-radio.md) | Radio (несколько визуальных видов) |
| [app-section-header.md](app-section-header.md) | Заголовки крупных секций |
| [preferences.md](preferences.md) | Preference kit (seamless save) |
| [auto-refresh.md](auto-refresh.md) | Фоновый poll без AppBar refresh |

Составные виджеты **обязаны** использовать одинаковые spacing tokens и атомы (`AppListItem`, кнопки) внутри.  
HelpSystem (справки) — вне этого модуля; в экранах нет instructional copy.
