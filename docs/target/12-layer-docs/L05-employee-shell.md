# L05 — Employee shell + cabinet entry

| Поле | Значение |
|------|----------|
| Status | doing |
| Quality | 7 |
| Quality note | Dev shell + SSE chat + tab interpreters + bundle I/O + meta columns |
| Plan | [L05](../11-implementation-plan/L05-employee-shell.md) |
| Canon | [04-employees](../04-employees/), [session](../10-identity-keycloak/session.md) |
| Last updated | 2026-08-23 — text/JSON preview + PDF stub viewer |
| Owners | — |

---

## Семантика

Контур сотрудника: cabinets → DynamicCabinetShell (meta L06). `X-Cabinet-Id` / `X-Project-Id` в API client.

## Что сделано

| Сделано | Gaps |
|---------|------|
| `ProdavanApi` client (Bearer + work headers) | |
| `WorkContext` singleton | |
| LoginPage + SessionGate + SessionStore (secure) | |
| OIDC PKCE — AppAuth mobile + desktop loopback | |
| ContourSelectorPage (multi-company) | |
| DevSessionPage (legacy) | |
| `CabinetCreatePage` full-page (no modal) | Custom bundle tab views (non-collection) |
| `CabinetRowEditPage` full-page row edit | |
| `ProjectCreatePage` full-page (no modal) + agent provider | |
| `ProjectSettingsPage` — name + provider override + pause/resume + rematerialize | |
| Import/export bundle UI + starter catalog import | |
| ProjectWorkspacePage — SSE chat + transcript + attachment preview (image/text/JSON + PDF stub) + inbox + suspend/paused banners + HITL + cancel | Real PDF renderer |
| Tables tab — row upsert/delete + DangerConfirm delete | |
| Chat tab → `ProjectListPage` | |
| Context tab — stats + export + AGENTS edit | |

## Gaps

| Требование | Статус | Заметка |
|------------|--------|---------|
| OIDC login | live | PKCE AppAuth + loopback; register redirect URIs in KC |
| Import bundle UI | live | `CabinetImportBundlePage` |
| Bundle export save | live | Context tab + `FilePicker.saveFile` |
| Row edit UI | live | `CabinetRowEditPage` full-page |
| Create project full-page | live | `ProjectCreatePage` |
| Meta columns on empty tables | live | `getMetaTable` |
| Custom bundle views | live (subset) | collection tabs via `table_slug` |
| Starter bundle import | live | catalog + shipped equipment-procurement zip |
| HITL tool approval UI | live | `ToolApprovePage` on `tool_approval_request` |
| Widget tests (status banner + image/text/PDF preview chips) | live (subset) | full workspace navigation E2E — hole |

## Карта кода

```text
apps/flutter/lib/features/employee/
  cabinet_create_page.dart
  cabinet_import_bundle_page.dart
  cabinet_row_edit_page.dart
  cabinet_tables_tab_page.dart
  cabinet_tab_host.dart
  project_create_page.dart
  project_settings_page.dart
  project_list_page.dart
  project_workspace_page.dart
  widgets/attachment_preview_kinds.dart
  widgets/attachment_preview_chip.dart
  widgets/attachment_image_viewer.dart
  widgets/project_status_banner.dart
  tool_approve_page.dart
```
