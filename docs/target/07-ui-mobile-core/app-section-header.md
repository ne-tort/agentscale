# AppSectionHeader

Слот заголовка **крупной секции** на экране. Не дублирует title отдельных preference/selector строк.

## Когда использовать

- На странице **≥2 группы** однотипных контролов (несколько preference tiles, несколько StatTile-блоков, несколько EntityCollection).
- Заголовок описывает **блок целиком**, а не одну строку.

## Когда не использовать

- Строка уже имеет собственный title (`ListTile`, `AppPreferenceTile`, `AppChoicePreference`) — **не** ставить `AppSectionHeader` с тем же текстом над ней.
- Одна-две настройки на странице (например Settings: язык + тема) — достаточно title в каждой строке.

## Примеры

| Экран | Секции с header | Без header |
|-------|-----------------|------------|
| Settings | — | Language, Theme (title в ListTile) |
| Admin company detail | Квоты, Политика агента, Подписка | — |
| Admin overview | Метрики (platform totals) | — |

## API

`AppSectionHeader({ required title, String? subtitle, Widget? trailing })` — `titleMedium` + optional muted subtitle.

См. также: [design-rules.md](design-rules.md), [preferences.md](preferences.md).
