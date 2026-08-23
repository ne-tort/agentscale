# L05 — Employee shell + cabinet entry

| Поле | Значение |
|------|----------|
| Status | doing |
| Quality | 7 |
| Quality note | Dev shell + SSE chat + tab interpreters + bundle I/O |
| Plan | [L05](../11-implementation-plan/L05-employee-shell.md) |
| Canon | [04-employees](../04-employees/), [session](../10-identity-keycloak/session.md) |
| Last updated | 2026-08-23 — row edit, chat tab, create cabinet page, export save |
| Owners | — |

---

## Семантика

Контур сотрудника: cabinets → DynamicCabinetShell (meta L06). `X-Cabinet-Id` / `X-Project-Id` в API client.

## Что сделано

| Сделано | Gaps |
|---------|------|
| `ProdavanApi` client (Bearer + work headers) | AppAuth OIDC login |
| `WorkContext` singleton | ContourSelectorPage |
| DevSessionPage (paste JWT) | Production secure storage |
| `CabinetCreatePage` full-page (no modal) | Create project still modal |
| Import/export bundle UI | Custom bundle tab views |
| ProjectWorkspacePage — SSE chat + transcript reload | |
| Tables tab — row upsert/delete | meta columns API for empty tables |
| Chat tab → `ProjectListPage` | |
| Context tab — stats + export save to file | |

## Gaps

| Требование | Статус | Заметка |
|------------|--------|---------|
| OIDC login | todo | L01 |
| Import bundle UI | live | `CabinetImportBundlePage` |
| Bundle export save | live | Context tab + `FilePicker.saveFile` |
| Row edit UI | live (subset) | inferred fields; dialog not full-page |
| Meta tab interpreters | live (subset) | projects/chat/tables/tools/context |
| Create project full-page | hole | still AlertDialog |
| Custom bundle views | hole | non-system tabs from imported bundles |
| Starter bundle catalog | hole | L04 read-only API + Admin UI |

## Карта кода

```text
apps/flutter/lib/features/employee/
  cabinet_create_page.dart
  cabinet_import_bundle_page.dart
  cabinet_tables_tab_page.dart
  cabinet_tab_host.dart
  project_list_page.dart
  project_workspace_page.dart
```
