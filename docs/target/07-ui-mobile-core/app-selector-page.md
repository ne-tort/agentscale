# AppCatalogSelectPage / AppSelectorPage

Полноэкранный picker в table/list UX. **Канон** — `AppCatalogSelectPage`.  
`AppSelectorPage` — thin wrap для back-compat (делегирует в catalog page).

## Возможности

| Опция | Тип | Описание |
|-------|-----|----------|
| `title` | String | AppBar title |
| `items` | `List<AppCatalogSelectItem>` | Данные |
| `multiSelect` | bool | Multi: trailing `AppSwitch`; single: `AppRadio` |
| `selectedIds` | `Set<String>` | Начальный выбор |
| `searchEnabled` | bool | Поле поиска сверху |
| `warningBanner` | String? | Плашка над списком |
| `empty` | Widget? | EmptyPlaceholder |
| `onConfirm` | `ValueChanged<Set<String>>` | Вызывается **сразу** при каждом изменении выбора (multi и single); внешней кнопки «Готово» нет |
| `popOnSelect` | bool | Single: выбрать и pop сразу |
| `allowCreate` / `allowEdit` / `allowDelete` | bool | Мутации каталога |
| `onCreate` / `onEdit` / `onDelete` | callbacks | Create через inline add; edit/delete после **long-press** |

## `AppCatalogSelectItem`

| Поле | Описание |
|------|----------|
| `id` | Стабильный id |
| `title` / `subtitle` | |
| `icon` / `leading` | Слева |
| `payload` | Произвольный JSON (каталог) |
| `enabled` | |

## Ряд

`AppListItem`: **leading + trailing одновременно**; `borderless`.  
Long-press → edit mode (highlight) + actions edit/delete.

## Enum vs catalog

- Static enum (`AppChoicePreference` / `AppMultiChoicePreference`): items in-memory, flags mutate = false.
- Editable catalog (AI HTTP providers `ai.http_providers`): `allowCreate/Edit/Delete: true`.

## Инварианты

- Нет `DropdownButton` для тех же items.
- Preferences открывают catalog page, не legacy popup menus.
- Multi-select: сохранение через `onConfirm` на каждый toggle; назад возвращает текущий `_selected`.
- Нет AppBar-кнопки «Готово» / внешних Save на picker.
