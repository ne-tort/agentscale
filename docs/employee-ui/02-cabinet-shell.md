# 02 — Cabinet shell

`CabinetShell` — зеркало `CompanyShell` / `AdminShell`.

## Wide (≥600px)

- Logo → `CabinetOverviewPage`
- Rail destinations:
  - **Projects** (system, order 10)
  - Module tabs with `nav.placement: rail` only
- **No** «Управление» destination on desktop
- Trailing: **Settings** → `EmployeeSettingsBody`

## Narrow (<600px)

- Bottom nav: **Управление** hub only (like CompanyShell)
- Hub list: module tabs with `nav.placement: management` (from parent shell — no reload)
- **Projects** — not in hub; available on wide rail only (or via overview/deep link later)
- Settings — trailing bottom item

## State machine

Копия `_CompanyShellState`: `_contentIndex`, `_railSelected`, `_narrowStackIndex`, `_subpageOpen`, `AppShellBranch` per tab.

## Params

```dart
CabinetShell({
  required String cabinetId,
  required String cabinetName,
})
```

Switch cabinet: `pushReplacement` новый `CabinetShell` или return to picker.
