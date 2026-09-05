# 02 — Cabinet shell

`CabinetShell` — зеркало `CompanyShell` / `AdminShell`.

## Wide (≥600px)

- Logo → `CabinetOverviewPage`
- Rail destinations:
  - **Projects** (system, order 10)
  - Module tabs with `nav.placement: rail` only (cabinet instance APIs)
  - **Управление** / **Данные** hubs only when the selected project has tabs for that placement
- Trailing: **Settings** → `EmployeeSettingsBody`

## Narrow (<600px)

- Bottom nav: **Projects**, **Overview**, optional **Управление** / **Данные**, Settings
- Hub lists: module tabs with `nav.placement: management` / `data` for the selected project
- Empty placement → hub destination omitted from nav
- Hub page with null `projectId` still shows CTA to create/select a project

## Runtime-modules scope

| Surface | Owner | API |
|---------|-------|-----|
| Rail tabs | cabinet instance | `/cabinets/{id}/modules…` |
| Management / Data | project leaf | `/projects/{id}/runtime-modules…` |

`CabinetModuleHost(projectId: …)` must receive an explicit project id for hubs; rail omits it. Do not fall back to `workContext.selectedProjectId` inside the host.

## Chat

- **Wide:** dedicated chat `Navigator` (`_chatNavKey`) overlaid on the shell body — **not** nested under Projects. Switching dialogs uses `pushReplacement` on that stack; Projects table stays on its own branch.
- **Narrow:** root push — bottom nav hidden (full-screen chat).
- Paused / error / pod-down: transcript stays readable; composer wake (warning text) — tap resumes or reloads without extra banners.

## State machine

Копия `_CompanyShellState`: `_contentIndex`, `_railSelected`, `_narrowStackIndex`, `_subpageOpen`, `AppShellBranch` per tab. Reload management/data nav when `selectedProjectId` changes.

## Params

```dart
CabinetShell({
  required String cabinetId,
  required String cabinetName,
})
```

Switch cabinet: `pushReplacement` новый `CabinetShell` или return to picker.
