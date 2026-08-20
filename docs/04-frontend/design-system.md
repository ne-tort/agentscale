# Дизайн-система Prodavan

Единая визуальная система Flutter-клиента: **минималистичный icon-first chrome**, плотная информационная сетка для операторов закупок, доступность без зависимости от цвета.

---

## Философия

1. **Icon-only navigation** — главная навигация (rail / bottom bar) показывает **только иконки**. Текстовые labels в nav **запрещены**.
2. **No tooltips on nav** — иконки навигации **не** показывают `Tooltip` / `Hint` при hover. Оператор знает иконки по muscle memory; название модуля — в заголовке экрана.
3. **Text in content** — подписи, таблицы, формы — полнотекстовые в рабочей области.
4. **Density over decoration** — оператор работает часами; лишние отступы и декоративные карточки минимизированы.

> **Исключение:** icon-only кнопки **внутри контента** (toolbar MD-редактора, action bar таблицы) тоже без tooltip — см. [md-editor.md](md-editor.md). Доступность через `Semantics(label: ...)`.

---

## AppSpacing

Базовая сетка: **4 px**. Все отступы — кратны 4.

| Token | Value | Применение |
|-------|-------|------------|
| `AppSpacing.xs` | 4 | Gap между иконками в группе, padding chip |
| `AppSpacing.sm` | 8 | Padding кнопки icon-only, gap list tile |
| `AppSpacing.md` | 16 | Padding card, section gap |
| `AppSpacing.lg` | 24 | Padding screen horizontal (mobile) |
| `AppSpacing.xl` | 32 | Section separator, empty state vertical |

```dart
abstract final class AppSpacing {
  static const double xs = 4;
  static const double sm = 8;
  static const double md = 16;
  static const double lg = 24;
  static const double xl = 32;
}
```

### Правила использования

- Screen padding: `lg` (mobile), `xl` (desktop ≥ 1024)
- Между секциями формы: `md`
- Icon nav rail item: `sm` internal padding, `md` между группами
- Table cell: `sm` vertical, `md` horizontal
- **Не** использовать произвольные значения (13, 20) — только токены или `xs * n`

---

## Цвета

| Token | Light | Dark | Назначение |
|-------|-------|------|------------|
| `primary` | `#1565C0` | `#64B5F6` | Акцент, active nav |
| `surface` | `#FAFAFA` | `#121212` | Background |
| `surfaceContainer` | `#FFFFFF` | `#1E1E1E` | Cards, panels |
| `onSurface` | `#212121` | `#E0E0E0` | Primary text |
| `onSurfaceVariant` | `#616161` | `#9E9E9E` | Secondary text |
| `error` | `#C62828` | `#EF5350` | Errors |
| `success` | `#2E7D32` | `#66BB6A` | OK status |
| `warning` | `#F57C00` | `#FFA726` | Warnings |

Статусы **дублируются текстом** — не только цветом (WCAG).

---

## Типографика

| Style | Size | Weight | Use |
|-------|------|--------|-----|
| `displaySmall` | 24 | 600 | Screen title |
| `titleMedium` | 16 | 600 | Section header |
| `bodyLarge` | 16 | 400 | Primary content |
| `bodyMedium` | 14 | 400 | Tables, secondary |
| `labelSmall` | 11 | 500 | Badges, counters |

Шрифт: **Inter** (web/desktop), system fallback (mobile).

---

## Icon-only navigation

### Nav rail (desktop / tablet landscape)

```text
┌────┬──────────────────────────┐
│ 🏠 │  Screen title            │
│ 📁 │                          │
│ 📋 │  Content                 │
│ ✏️ │                          │
│ ...│                          │
└────┴──────────────────────────┘
 48px   flex
```

- Ширина rail: **48 px** (compact) или **56 px** (touch)
- Иконка: **24 px**, centered
- Active: `primary` fill background pill **без** text label
- Semantics: `Semantics(label: 'M01 Проекты', button: true)`

### Bottom bar (mobile portrait)

- Max **5** primary icons; остальные — «Ещё» (overflow sheet с **текстом** — единственное место, где nav имеет labels на mobile)
- Height: 56 px + safe area

### Запрещено в nav chrome

- `Tooltip(message: ...)`
- `MouseRegion` с текстовым hint overlay
- Text под иконкой (`NavigationDestination.label` = `''`)

---

## Компоненты

### IconButton (standard)

- Size: 40×40 touch target
- Icon: 20–24 px
- Padding: `AppSpacing.sm`
- **Без** tooltip; `Semantics` обязателен

### AppCard

- Radius: 8 px
- Elevation: 0 (border `outlineVariant` 1 px)
- Padding: `AppSpacing.md`

### AppDataTable

- Dense mode default
- Sticky header на desktop
- Row height: 40 px (dense), 48 px (comfortable — settings)

### StatusBadge

- Icon + short text («OK», «Ошибка», «Лимит»)
- Padding: `xs` horizontal, `xs` vertical

---

## Тема кабинета (cabinet pack)

Pack может переопределить:
- `primary` color
- Логотип в header (не в nav rail)
- **Не может** переопределить spacing tokens и icon-only nav rule

Seed: `packages/cabinet-packs/{profile}/theme/colors.json`

---

## Dark mode

- Следует system preference
- Toggle в user settings (M08)
- Golden tests — оба режима для nav rail и MD editor toolbar

---

## Accessibility checklist

- [ ] Все icon-only controls имеют `Semantics(label:)`
- [ ] Focus ring visible (keyboard)
- [ ] Contrast ratio ≥ 4.5:1 для body text
- [ ] Live regions для SSE streaming (M07)
- [ ] Nav: `Alt+1`…`Alt+0` shortcuts к модулям M00–M09

---

## Связанные документы

- [widget-catalog.md](widget-catalog.md)
- [cabinet-shell.md](cabinet-shell.md)
- [md-editor.md](md-editor.md)
- [responsive.md](responsive.md)
