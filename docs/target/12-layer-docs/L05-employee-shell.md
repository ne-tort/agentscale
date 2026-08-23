# L05 — Employee shell + cabinet entry

| Поле | Значение |
|------|----------|
| Status | doing |
| Quality | 7 |
| Quality note | Dev shell + SSE chat + attachments + tab interpreters |
| Plan | [L05](../11-implementation-plan/L05-employee-shell.md) |
| Canon | [04-employees](../04-employees/), [session](../10-identity-keycloak/session.md) |
| Last updated | 2026-08-23 — import bundle UI + export from context tab |
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
| CabinetListPage + create cabinet | Custom bundle tab views |
| Import bundle full-page (`CabinetImportBundlePage`) | |
| Export bundle from Context tab | |
| ProjectWorkspacePage — SSE chat + transcript reload | |
| `uploadProjectAttachment` + attachment chips | |
| `projectChatStream` abort via HTTP client close | |
| `attachment_refs` forwarded to chat API | |
| Cancel + abort SSE in workspace | |
| Live + replay `tool_call` bubbles | |
| Tables/Tools tab interpreters (read-only) | |
| Context tab — cabinet summary stats | |

## Как сделано

1. `core/api/prodavan_api.dart` — cabinets meta/tables/rows, MCP tools, chat API.
2. `features/employee/*` — `CabinetTabHost` routes `projects|tables|tools|chat|context`.
3. `events_to_transcript` emits `role=tool` for persisted `tool_call` events.
4. Entry from `app.dart` → Dev session (dev only until L01 cutover).

## Контракты

| ID | Статус |
|----|--------|
| C-EMP-SHELL | **live** (subset) |

## Карта кода

```text
apps/flutter/lib/
  core/api/prodavan_api.dart
  core/session/work_context.dart
  features/employee/{dev_session,cabinet_list,cabinet_import_bundle_page,dynamic_cabinet_shell,cabinet_tab_host,
    cabinet_context_tab_page,cabinet_tables_tab_page,cabinet_tools_tab_page,project_list,project_workspace}_page.dart
```

## Gaps

| Требование | Статус | Заметка |
|------------|--------|---------|
| OIDC login | todo | L01 |
| Peer isolation UI test | hole | |
| Import bundle UI | live | file_picker → POST `/cabinets/import` |
| Bundle export in UI | live | Context tab → GET `/cabinets/{id}/bundle` |
| Chat streaming | live | SSE text_delta in workspace |
| Attachment upload in chat | live | file_picker → POST `/attachments` → chips → `attachment_refs` |
| In-flight SSE abort | live | `ProjectChatStreamHandle.abort()` closes HTTP client |
| Event history reload | live | `GET .../chat/transcript` incl. user + tool bubbles |
| Meta tab interpreters | live (subset) | projects/tables/tools/context; chat placeholder |
| Custom bundle views | hole | non-system tabs from imported bundles |
| Row edit UI | hole | read-only tables tab |
