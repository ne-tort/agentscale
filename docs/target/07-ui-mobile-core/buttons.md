# Buttons — унификация

Все кнопки — из core. Feature не создаёт локальные `IconButton`/`TextButton`/`FilledButton` со своими стилями.

## Виды

| Виджет (канон) | Назначение |
|----------------|------------|
| `AppIconButton` | Chrome, toolbar, secondary actions — **по умолчанию** |
| `AppIconToggle` | Режимы (list/table, filter); меняет selected/цвет |
| `AppButton` | Labeled primary / явно значимое действие |

Существующий `AppButton` в коде покрывает labeled; icon/toggle — целевые core-виджеты (или явные обёртки над ним).

## Когда label

Только если:
- действие primary на форме (Создать / Сохранить), или
- иконка семантически неоднозначна для пользователя.

## Текст label (строго)

- Только **короткие существительные** (или устоявшиеся noun-команды): «Экспорт», «Импорт», «Создать», «Сохранить», «Удалить».
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
- Дублировать одну и ту же action и icon, и длинным label без нужды.
- Feature-specific цвета кнопок в обход `AppColorTokens`.
