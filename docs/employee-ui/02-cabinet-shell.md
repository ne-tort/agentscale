# 02 — Cabinet shell

`CabinetShell` — зеркало `CompanyShell` / `AdminShell`.

## Wide (≥600px)

- Logo → `CabinetOverviewPage`
- Rail destinations:
  - **Projects** (system, order 10)
  - Module tabs with `nav.placement: rail` only (cabinet instance APIs)
  - **Управление** / **Данные** hubs when that placement has entries (cabinet modules without a project; bound modules when a project is selected)
- Trailing: **Settings** → `EmployeeSettingsBody`

## Narrow (<600px)

- Bottom nav: **Projects**, **Overview**, optional **Управление** / **Данные**, Settings
- Hub lists: same rules as wide — cabinet tabs without project; project-bound tabs when selected
- Empty placement → hub destination omitted from nav

## Runtime-modules scope

| Surface | No project | Project selected |
|---------|------------|------------------|
| Rail tabs | Cabinet modules | Cabinet modules (`placement: rail`) |
| Management / Data | All cabinet module tabs for that placement | Only modules with MP; SoT = cabinet if `bind_kind=global`, project leaf if `local` |

`CabinetModuleHost(projectId: …)` for **local** binds only; **global** opens cabinet APIs (`projectId` null). Do not fall back to `workContext.selectedProjectId` inside the host.

## Chat

- **Wide:** dedicated chat `Navigator` (`_chatNavKey`) overlaid on the shell body — **not** nested under Projects. Switching dialogs uses `pushReplacement` on that stack; Projects table stays on its own branch.
- **Narrow:** root push — bottom nav hidden (full-screen chat).
- Paused / error / pod-down: transcript stays readable; composer wake (warning text) — tap resumes or reloads without extra banners.

### Active chat (`selectedSessionId`)

Independent of main-rail selection (same idea as `selectedProjectId`):

| Selection | Where | Cleared when |
|-----------|-------|--------------|
| Main destination (Projects / Management / Data / …) | `AppLayout.selectedIndex` | Switch destination / overview / settings |
| Active chat | `WorkContext.selectedSessionId` → chats rail highlight | Project change, cabinet enter, logout, or session gone from sidebar |

Opening a chat sets `selectedSessionId` and **keeps** it after leaving the chat overlay — hubs with `scope.active_chat: required` and `scope.chats=current` data stay bound to that session until another chat is chosen or the selection is cleared.

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
