# M07 — Agent Runtime

Модуль **чата агента per project**: SSE/WebSocket стрим событий, персистентная история, вложения → inbox, команды reset/cancel/model.

## Scope

| функция | описание |
| --- | --- |
| Chat per project | Одна agent session на `project_id` per user (configurable) |
| Stream | SSE (default) + WebSocket (bidirectional) |
| History | PostgreSQL + optional object storage для больших payloads |
| Attachments | Upload → `projects/{slug}/inbox/` |
| Control | `/reset`, cancel run, switch model |

## Зависимости

```text
M08 (JWT, RBAC, tenant isolation)
M06 (MCP session-bind)
M05 (integration context badge)
M09 (audit, token accounting)
```

## Event layers

См. Commerce cursor-sdk-event-contract, адаптировано для Prodavan:

| layer | events | UI |
| --- | --- | --- |
| stable | `assistant`, `status`, `tool_call.*`, `run.completed` | основной чат |
| extended | `thinking`, `shell_output`, tool deltas | debug drawer |
| system | `session.reset`, `model.changed`, `error` | toast/banner |

## Документация

- [domain.md](domain.md) — Session, Message, Run, Attachment
- [api.md](api.md) — REST + SSE + WS
- [persistence.md](persistence.md)
- [storage.md](storage.md) — inbox, attachments
- [mcp-tools.md](mcp-tools.md) — agent-side (minimal)
- [ui.md](ui.md)
- [security.md](security.md)
- checklists

## Инварианты

1. **cwd агента** = корень project (`inbox/`, `runs/`)
2. История не теряется при reconnect SSE
3. Cancel aborts provider run + MCP calls in-flight
4. Reset = new agent_id, MCP re-bind, history archived
5. Attachments только в inbox текущего project
