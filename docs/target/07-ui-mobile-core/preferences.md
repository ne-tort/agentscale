# Preference kit — seamless save controls

Набор виджетов в `apps/flutter/lib/core/preferences/` для экранов настроек и форм с выбором значений без модальных диалогов.

## Компоненты

| Виджет | Назначение |
|--------|------------|
| `AppPreferenceTile` | Базовая строка: title, subtitle, icon, trailing, onTap |
| `AppPreferenceSection` | Группа контролов под `AppSectionHeader` |
| `AppChoicePreference<T>` | Выбор из списка через `AppCatalogSelectPage` (radio) |
| `AppMultiChoicePreference` | Множественный выбор через `AppCatalogSelectPage` (switch) |
| `AppValuePreference<T>` | Inline edit текста/числа с валидацией и `onSave` |
| `AppSwitchPreference` | Boolean toggle с немедленным `onChanged` |
| `AppNavPreference` | Hub-строка с chevron → подстраница |
| `AppSubscriptionPreference` | Дата (DD.MM.YY): пусто = unlimited / not set |
| `AppInlineAddField` | Inline add в списках сущностей (Hiddify clients pattern); full-bleed divider под полем |

Barrel: `package:prodavan/core/preferences/preferences.dart` (preference tiles).  
`AppInlineAddField` живёт в `lib/core/widgets/` (экспорт из `widgets.dart`).

## Create vs settings

| Контекст | Паттерн |
|----------|---------|
| Список сущностей (companies, AI keys) | `AppInlineAddField` → create по имени → detail |
| Detail/settings | Preference kit → PATCH сразу (нет page Save) |

## AppChoicePreference

Открывает полноэкранный `AppCatalogSelectPage`, возвращает выбор и вызывает `onSave`.

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
| Inline create (`AppInlineAddField`) | API create сразу; остальное на detail |
| Оставшиеся multi-field create-формы | Только `setState` — значение уходит на API при submit |
| Detail/settings (seamless save) | `setState` + немедленный PATCH/PUT |

## AppValuePreference / AppSwitchPreference

Используются на detail-страницах (например `AdminCompanyDetailPage`): каждое изменение сразу сохраняется на сервер.

## Запреты

- **Не** использовать `DropdownButton` / `DropdownButtonFormField` — только `AppChoicePreference` или `AppCatalogSelectPage`.
- Create/submit и detail: preference kit (`AppValuePreference`, `AppChoicePreference`, …) + `AppButton` / `AppAsyncButton`.
- **Не** заводить параллельные `AppTextField` / `AppPasswordField` / `AppForm` — ввод текста и секретов = `AppValuePreference` (в т.ч. `obscureText: true`).
- Feature **не** создаёт локальные `IconButton`/`TextButton`/`FilledButton` — только core (`AppIconButton`, `AppButton`).

## Связь

- [app-selector-page.md](app-selector-page.md)  
- [design-rules.md](design-rules.md)  
- [app-section-header.md](app-section-header.md)
