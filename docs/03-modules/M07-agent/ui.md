# M07 — UI

## Project chat view

Route: `/cabinet/{cid}/projects/{slug}/chat`

### Layout

```text
┌─────────────────────────────────────────────┐
│ Header: project name | model ▼ | MCP chip   │
├─────────────────────────────────────────────┤
│ Message list (virtualized)                  │
│   user / assistant / tool cards             │
├─────────────────────────────────────────────┤
│ Attachment bar | composer | Send            │
└─────────────────────────────────────────────┘
│ Debug drawer (collapsible)                  │
└─────────────────────────────────────────────┘
```

## Message rendering

| role | UI |
| --- | --- |
| user | right-aligned bubble |
| assistant | markdown, code blocks |
| tool | collapsed card: tool name, status icon, expand summary |
| system | centered muted (reset, model change) |

### Tool card states

- running: spinner
- ok: green check
- error: red + error code

## Streaming UX

1. User sends → optimistic user message
2. SSE connects → status «Думаю…»
3. `assistant.delta` → typewriter append
4. `tool_call.*` → insert tool card inline
5. `run.completed` → enable composer

### Reconnect

On disconnect: banner «Переподключение…», auto-retry with `Last-Event-ID`.

## Controls

| control | action | shortcut |
| --- | --- | --- |
| Stop | cancel run | Esc |
| Reset session | confirm modal | — |
| Model picker | dropdown tenant models | — |
| Debug | toggle extended stream | Ctrl+D |

## Attachments

- Drag-drop onto composer
- Paperclip → file picker
- List in sidebar «Inbox» with link to open/download
- xlsx badge «extracted.md готов»

## Inbox sidebar

Table: filename, size, uploaded_at, «Использовать в сообщении»

## Mobile

- Full screen chat
- Debug drawer hidden
- Attachments via camera/files

## Accessibility

- Live region for streaming text
- Tool cards: aria-expanded
- Cancel button always keyboard reachable during run

## Error states

| error | UI |
| --- | --- |
| RUN_ALREADY_ACTIVE | Disable send, show Stop |
| MODEL_NOT_ALLOWED | Model picker error toast |
| STREAM_REPLAY_GAP | Full history reload button |
