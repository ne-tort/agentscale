# Buttons — унификация

Все кнопки — из core. Feature не создаёт локальные `IconButton`/`TextButton`/`FilledButton` со своими стилями.

## Виды

| Виджет (канон) | Назначение |
|----------------|------------|
| `AppIconButton` | Chrome, toolbar, secondary actions — **по умолчанию** |
| `AppIconToggle` | Режимы (list/table, filter); меняет selected/цвет |
| `AppNavPreference` | Labeled action / hub-строка (как «Язык», «Тема», «Вход», «Сохранить») |

`AppButton` / `AppAsyncButton` **удалены** — filled Material-кнопки не канон.

## Когда label-строка

Только если:
- действие primary на форме (Создать / Сохранить / Вход), или
- иконка семантически неоднозначна для пользователя.

Иначе — `AppIconButton` в toolbar.

## Текст title (строго)

- Только **короткие существительные** (или устоявшиеся noun-команды): «Экспорт», «Импорт», «Создать», «Сохранить», «Удалить», «Вход».
- **Запрещено:** «Экспортировать», «Импортировать», «Нажмите чтобы сохранить», фразы из >2–3 слов.

## Icon buttons

- Обязателен `tooltip` / `semanticLabel` (a11y) — это не instructional UI-copy на экране.
- Hit target ≥ 48×48 (см. spacing / responsive).
- В toolbar EntityCollection — icon-first.

## Toggle

- Два+ взаимоисключающих или on/off режима.
- Visual selected state обязателен (цвет / filled).
- Не подписывать toggle длинным текстом рядом «режим таблицы для удобства…».

## Запрещено

- Длинные подписи на кнопках.
- `FilledButton` / `OutlinedButton` / `TextButton` в feature.
- Feature-specific цвета кнопок в обход `AppColorTokens` / `accentColor` у preference.
