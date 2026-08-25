# Preference kit — seamless save controls

Набор виджетов в `apps/flutter/lib/core/preferences/` для экранов настроек и форм с выбором значений без модальных диалогов.

## Компоненты

| Виджет | Назначение |
|--------|------------|
| `AppPreferenceTile` | Базовая строка: title, subtitle, icon, trailing, onTap |
| `AppPreferenceSection` | Группа контролов под `AppSectionHeader` |
| `AppChoicePreference<T>` | Выбор из списка через `AppSelectorPage` (radio / checkbox) |
| `AppValuePreference<T>` | Inline edit текста/числа с валидацией и `onSave` |
| `AppSwitchPreference` | Boolean toggle с немедленным `onChanged` |
| `AppNavPreference` | Hub-строка с chevron → подстраница |
| `AppSubscriptionPreference` | Дата окончания: пусто = бессрочно |
| `AppMultiChoicePreference` | Множественный выбор через `AppSelectorPage` |
| `AppInlineAddField` | Inline add в списках сущностей (Hiddify clients pattern) |

Barrel: `package:prodavan/core/preferences/preferences.dart`.

## AppChoicePreference

Открывает полноэкранный `AppSelectorPage`, возвращает выбор и вызывает `onSave`.

```dart
AppChoicePreference<String>(
  title: l10n.adminToolPreset,
  icon: Icons.tune_rounded,
  value: _toolPreset,
  choices: _toolPresets,
  keyFor: (v) => v,
  labelFor: (v) => v,
  iconFor: (v) => Icons.developer_mode_outlined,
  onSave: (v) async {
    setState(() => _toolPreset = v);
    await _persistToApi(v);
  },
)
```

### Create-flow vs settings

| Контекст | `onSave` |
|----------|----------|
| Страница создания (submit по кнопке Create/Save) | Только `setState` — значение уходит на API при submit формы |
| Detail/settings (seamless save) | `setState` + немедленный PATCH/PUT |

## AppValuePreference / AppSwitchPreference

Используются на detail-страницах (например `AdminCompanyDetailPage`): каждое изменение сразу сохраняется на сервер.

## Запреты

- **Не** использовать `DropdownButton` / `DropdownButtonFormField` — только `AppChoicePreference` или `AppSelectorPage`.
- **Не** использовать удалённые `AppTextField`, `AppPasswordField`, `AppForm`.
- Create/submit-формы: `Form` + `TextFormField` + явная кнопка Create/Save.
- Dev session: `TextField` + `InputDecoration` (без `FormState`).

## Связь

- [app-selector-page.md](app-selector-page.md)  
- [design-rules.md](design-rules.md)  
- [app-section-header.md](app-section-header.md)
