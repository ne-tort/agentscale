# 06 — Employee settings

`EmployeeSettingsBody` → `SettingsPage(embedded: true)`.

## leadingChildren

1. **Login** — read-only (нельзя менять)
2. **Password** — `PUT /me/password` (self-service)
3. **Contact email** — `PATCH /me/contact-email`
4. **Company name** — from `/me` employee membership, read-only

## Switch cabinet

- `AppNavPreference` → `CabinetPickerPage` или list dialog
- On select: `workContext.enterCabinet` + `pushReplacement(CabinetShell)`

## Trailing (from SettingsPage)

- Language, theme, auto-refresh, sign out — без изменений
