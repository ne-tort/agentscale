# M07 — MCP tools (agent module)

M07 **не экспонирует** доменные MCP tools — использует M06 session-bind. Ниже — **meta-tools** runtime (optional server `prodavan-agent`).

## prodavan-agent (optional, admin-debug only)

| tool | описание |
| --- | --- |
| `agent.get_session_info` | session_id, model, profile, mcp_snapshot_hash |
| `agent.list_inbox` | файлы в inbox проекта |
| `agent.read_inbox_text` | прочитать `.extracted.md` или txt |

Не включать в profile `kp` — агент использует встроенный filesystem в cwd.

## Provider integration

Agent runtime вызывает:

1. `POST /internal/v1/mcp/session-bind` (M06)
2. Provider SDK `Agent.create({ mcpServers, model, instructions })`
3. `Agent.prompt()` / stream subscription

## Instructions assembly

```text
{platform AGENTS.md}
{project AGENTS.md snapshot}
{cabinet integration summary — no secrets}
Effective tools: {count} from profile {profile_id}
```

## Event normalization

Provider raw event → `StreamEvent`:

| provider | normalized |
| --- | --- |
| `assistant` text delta | `assistant.delta` |
| tool start | `tool_call.started` |
| tool end | `tool_call.completed` (summary only, redacted) |
| thinking | `thinking.delta` (if flag) |

`eventParserVersion`: `2026.08.1`

## Redaction rules

Strip from tool payloads before persist/stream:

- JWT, API keys
- S4B credentials
- Full file contents > 4 KB → truncate + ref

## Commerce parity

| Commerce Telegram | Prodavan M07 |
| --- | --- |
| `/reset` | POST session/reset or WS reset |
| `/кп` | bot command → separate module (not agent) |
| file upload to inbox | POST attachments |
| stream to chat | SSE/WS |
| `proj:userId:project` session key | session per project+user |
