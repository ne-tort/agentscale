# L05 — Employee shell + cabinet entry

| Поле | Значение |
|------|----------|
| Status | doing |
| Quality | 7 |
| Quality note | Dev shell + SSE chat workspace + transcript reload |
| Plan | [L05](../11-implementation-plan/L05-employee-shell.md) |
| Canon | [04-employees](../04-employees/), [session](../10-identity-keycloak/session.md) |
| Last updated | 2026-08-23 — attachment picker + SSE abort |
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
| CabinetListPage + create cabinet | Import bundle UI |
| DynamicCabinetShell tabs from meta | Dynamic tab content interpreters |
| ProjectWorkspacePage — SSE chat + transcript reload | AppAuth OIDC login |
| `uploadProjectAttachment` + attachment chips in chat | ContourSelectorPage |
| `projectChatStream` → abort via HTTP client close | Dynamic tab content interpreters |
| `attachment_refs` forwarded to chat API | Import bundle UI |
| Cancel agent session UI | done | stop + abort SSE |

## Как сделано

1. `core/api/prodavan_api.dart` — `/me`, `/cabinets`, meta tabs, projects, `uploadProjectAttachment`, `projectChatStream` (abortable), `projectChatTranscript`.
2. `features/employee/*` — list → shell → projects → chat workspace (reload on open).
3. `AppScaffold.bottom` extended for TabBar.
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
  features/employee/{dev_session,cabinet_list,dynamic_cabinet_shell,project_list,project_workspace}_page.dart
```

## Gaps

| Требование | Статус |
|------------|--------|
| OIDC login | todo (L01) |
| Peer isolation UI test | hole |
| Import bundle UI | hole |
| Chat streaming | live | SSE text_delta in workspace |
| Attachment upload in chat | live | file_picker → POST `/attachments` → chips → `attachment_refs` |
| In-flight SSE abort | live | `ProjectChatStreamHandle.abort()` closes HTTP client |
| Event history reload | live (`GET .../chat/transcript`) |
| Pre-user_message legacy sessions | hole — assistant-only bubbles until re-chat |

## Quality | **6** | doing |
