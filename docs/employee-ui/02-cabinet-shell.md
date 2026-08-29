# 02 — Cabinet shell

`CabinetShell` — зеркало `CompanyShell` / `AdminShell`.

## Wide (≥600px)

- Logo → `CabinetOverviewPage`
- Rail destinations:
  - **Projects** (system, order 10)
  - Dynamic entries from `CabinetNavLoader` (module tabs)
- Trailing: **Settings** → `EmployeeSettingsBody`

## Narrow (<600px)

- Bottom nav: Management hub
- Hub → push Projects / module pages
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
