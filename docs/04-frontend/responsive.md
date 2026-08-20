# Адаптивная вёрстка

Стратегия responsive layout для Flutter-клиента Prodavan: **mobile-first** с progressive enhancement на tablet и desktop. Операторы закупок работают преимущественно на desktop; mobile — read-only обзор и чат.

---

## Breakpoints

| Token | Min width | Target devices |
|-------|-----------|----------------|
| `compact` | 0 | Phone portrait |
| `medium` | 600 | Phone landscape, small tablet |
| `expanded` | 1024 | Tablet landscape, laptop |
| `large` | 1440 | Desktop monitor |
| `extraLarge` | 1920 | Wide monitors |

```dart
enum AppBreakpoint {
  compact,    // < 600
  medium,     // 600–1023
  expanded,   // 1024–1439
  large,      // 1440–1919
  extraLarge, // ≥ 1920
}

AppBreakpoint breakpointOf(double width) {
  if (width >= 1920) return AppBreakpoint.extraLarge;
  if (width >= 1440) return AppBreakpoint.large;
  if (width >= 1024) return AppBreakpoint.expanded;
  if (width >= 600) return AppBreakpoint.medium;
  return AppBreakpoint.compact;
}
```

Использовать `LayoutBuilder` или `ResponsiveBuilder` из `core/widgets`.

---

## Navigation adaptation

| Breakpoint | Nav pattern | Width |
|------------|-------------|-------|
| compact | Bottom `NavigationBar` (icon-only) | full |
| medium | Bottom bar или rail (user pref) | — |
| expanded+ | Left `IconNavRail` | 48 px |

### Bottom bar overflow

Max 5 icons visible. Overflow → «Ещё» opens **modal sheet** с текстовыми labels (единственное место с text nav на mobile).

---

## Layout patterns

### Master-detail

| Breakpoint | Pattern |
|------------|---------|
| compact | Separate routes (list → push detail) |
| medium | List full width; detail as bottom sheet |
| expanded+ | `SplitPane` 360 px / flex |

Применение: M01 projects, M02 runs, M04 catalogs, M03 file tree.

### Settings forms

| Breakpoint | Max content width |
|------------|-------------------|
| compact | 100% |
| expanded+ | 720 px centered |

Применение: M05 integrations, M08 users, M00 cabinet settings.

### Data tables

| Breakpoint | Behavior |
|------------|----------|
| compact | Card list (one row = one card) |
| medium | Horizontal scroll table |
| expanded+ | Full table + sticky header |

Применение: M02 variants, M05 web shops, M09 audit.

---

## Screen-specific rules

### M07 Chat

| Breakpoint | Layout |
|------------|--------|
| compact | Full screen chat; attachments via bottom sheet |
| expanded | Chat 60% + inbox sidebar 40% |
| large | Max chat width 900 px centered in pane |

### M03 MD Editor

| Breakpoint | Layout |
|------------|--------|
| compact | Tab: Editor / Preview / Files |
| expanded | Tree + Editor + Preview (3-pane) |

### M02 Spec run review

| Breakpoint | Layout |
|------------|--------|
| compact | Phase stepper vertical |
| expanded | Phase stepper horizontal + split items/variants |

---

## Typography scaling

Базовые размеры **не** масштабируются fluidly — фиксированы для density.

Accessibility: respect system `textScaleFactor` до **1.3**; выше — horizontal scroll допустим.

---

## Touch vs pointer

| Element | Min touch target |
|---------|------------------|
| Icon nav item | 48×48 |
| AppIconButton | 40×40 (visual), 48×48 hit test |
| Table row | 48 height |
| List tile | 48 height |

Hover states — только `MouseRegion` on expanded+ (desktop/web).

---

## Platform specifics

| Platform | Notes |
|----------|-------|
| **Web** | URL sync via go_router; warn on tab close if dirty editor |
| **Windows/macOS/Linux** | Min window 800×600; native menu optional v2 |
| **Android/iOS** | Safe area insets; bottom nav padding |

---

## Window size classes (Material 3 alignment)

| M3 class | Prodavan token |
|----------|----------------|
| Compact | `compact` |
| Medium | `medium` |
| Expanded | `expanded` |
| Large | `large` |
| Extra-large | `extraLarge` |

---

## Performance

- Virtualized lists (`ListView.builder`) для > 50 items
- `RepaintBoundary` на chat bubbles streaming
- Defer heavy preview render (M03) until expanded breakpoint

---

## Тест matrix

Golden tests at widths: **375**, **768**, **1280**, **1920** px для:
- IconNavRail vs BottomBar
- M07 chat layout
- M03 editor panes
- M02 variant table → card list

---

## Связанные документы

- [design-system.md](design-system.md)
- [screens-inventory.md](screens-inventory.md)
- [architecture.md](architecture.md)
