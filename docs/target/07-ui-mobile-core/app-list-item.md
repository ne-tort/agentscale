# AppListItem

Универсальный атом **строки** списка. В list-режиме [EntityCollection](entity-collection.md); также внутри `AppSelectorPage`.

## Возможности (опции)

| Опция | Тип | Описание |
|-------|-----|----------|
| `title` | String / Widget | Основной идентификатор / имя |
| `subtitle` | String / Widget? | **Только данные** (status, id, meta) — не UX-инструкция |
| `leading` | Widget? | Левая иконка / аватар / custom |
| `trailing` | Widget? | Правый виджет (chevron, chip, switch UI — без modal) |
| `selected` | bool | Подсветка выбранного |
| `enabled` | bool | Disabled style + no tap |
| `tone` | enum | `neutral` \| `warning` \| `danger` \| `success` — цвет фона/бордера |
| `banner` | String? | **Status/tone** плашка (факт риска/состояния), не help-text |
| `onTap` | VoidCallback? | |
| `selectionControl` | Widget? | Встроенный `AppCheckbox` / `AppRadio` слева или вместо leading |
| `dense` | bool | Компактный режим |
| `semanticLabel` | String? | a11y |

## Синтаксис (целевой API)

```dart
AppListItem(
  title: Text('Cursor pool A'),
  subtitle: Text('cursor · cursor_sdk'), // data meta
  leading: Icon(Icons.key),
  trailing: Icon(Icons.chevron_right),
  tone: AppListTone.neutral,
  onTap: () => …,
)

AppListItem(
  title: Text('Отключить сотрудника'),
  tone: AppListTone.danger,
  banner: 'Доступ будет закрыт', // status consequence, не «как пользоваться»
  onTap: () => Navigator.push(… DangerConfirmPage …),
)
```

## Запрещено в subtitle / banner

- «Нажмите чтобы…», «Выберите проект для…», обучающие подсказки.
- Длинные абзацы.

## Согласованность

Padding из [spacing-tokens.md](spacing-tokens.md). Не оборачивать в Card без необходимости interaction container.  
Laconic: [principles.md](principles.md) §4.
