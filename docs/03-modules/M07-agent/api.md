# M07 — HTTP / SSE / WebSocket API

## Projects

### GET `/api/v1/cabinets/{cabinet_id}/projects`

### POST `/api/v1/cabinets/{cabinet_id}/projects`

**Body:** `{ "slug": "zakupka-08", "display_name": "..." }`

### GET `/api/v1/projects/{project_id}`

## Sessions

### GET `/api/v1/projects/{project_id}/session`

Current active session for caller.

### POST `/api/v1/projects/{project_id}/session/reset`

**Response 201:** new session object.

## Messages

### GET `/api/v1/sessions/{session_id}/messages`

**Query:** `?after_sequence=100&limit=50`

### POST `/api/v1/sessions/{session_id}/messages`

**Body:**

```json
{
  "content": "Обработай inbox/spec.xlsx",
  "attachments": ["attachment_uuid"]
}
```

**Response 202:**

```json
{
  "run_id": "uuid",
  "stream_url": "/api/v1/runs/{run_id}/stream"
}
```

## Runs

### GET `/api/v1/runs/{run_id}`

Status + token_usage.

### POST `/api/v1/runs/{run_id}/cancel`

**Response 200:** `{ "status": "cancelled" }`

### PATCH `/api/v1/sessions/{session_id}/model`

**Body:** `{ "model": "gpt-4.1" }`

## SSE Stream

### GET `/api/v1/runs/{run_id}/stream`

Headers:

```http
Accept: text/event-stream
Authorization: Bearer {jwt}
Last-Event-ID: 1005
```

Events (SSE format):

```text
id: 1006
event: assistant.delta
data: {"text":"Начинаю"}

id: 1007
event: tool_call.started
data: {"tool":"pipeline.new_run","call_id":"tc_1"}
```

**Resume:** client sends `Last-Event-ID` → server replays from `stream_events.sequence`.

### GET `/api/v1/sessions/{session_id}/stream`

Multiplex active + future runs for session (long-lived connection).

## WebSocket

### `/ws/v1/sessions/{session_id}`

**Client → Server:**

```json
{ "type": "message", "content": "...", "attachments": [] }
{ "type": "cancel", "run_id": "uuid" }
{ "type": "reset" }
{ "type": "set_model", "model": "..." }
{ "type": "ping" }
```

**Server → Client:** same event types as SSE in `{ "type", "payload", "sequence" }`.

## Attachments

### POST `/api/v1/projects/{project_id}/attachments`

`multipart/form-data`, field `file`.

**Response 201:**

```json
{
  "id": "uuid",
  "storage_path": "inbox/spec.xlsx",
  "extracted_md_path": "inbox/spec.xlsx.extracted.md"
}
```

Side effect: xlsx/csv → async extract → `.extracted.md` (Commerce parity).

### GET `/api/v1/projects/{project_id}/attachments`

## Internal

### POST `/internal/v1/agent/ingest-event`

Provider webhook / worker pushes raw SDK events → normalized StreamEvents.

## Error codes

| code | HTTP |
| --- | --- |
| `SESSION_ARCHIVED` | 409 |
| `RUN_ALREADY_ACTIVE` | 409 |
| `MODEL_NOT_ALLOWED` | 403 |
| `ATTACHMENT_TOO_LARGE` | 413 |
| `STREAM_REPLAY_GAP` | 410 |

## Rate limits

- 30 messages/min per user per project
- 3 concurrent SSE connections per user
- Attachment max 25 MB (configurable)
