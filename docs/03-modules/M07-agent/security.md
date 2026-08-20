# M07 — Безопасность

## Isolation

- Session scoped to `(project_id, user_id, cabinet_id)`
- JWT validates all three on every stream/message
- Agent filesystem sandbox = project root only
- No cross-project reads in tool results

## Stream safety

| risk | mitigation |
| --- | --- |
| Secret leak in tool output | redaction middleware |
| XSS in assistant markdown | sanitize HTML |
| SSE hijacking | JWT + short-lived stream token |
| WS DoS | 3 conn limit, message rate limit |

### Stream token

Optional query param for SSE (EventSource can't set headers):

```text
GET /runs/{id}/stream?token={signed_jwt_5min}
```

## Attachments

- MIME allowlist: xlsx, xls, csv, txt, pdf, png, jpg
- Block: `.exe`, `.js`, `.html`, macro-enabled xls
- ClamAV scan async
- Extracted md — plain text only

## Cancel / reset authorization

- Cancel: session owner or `cabinet.admin`
- Reset: session owner only (unless admin override config)

## Model allowlist

Tenant config:

```json
{
  "allowed_models": ["claude-sonnet-4", "gpt-4.1"],
  "default_model": "claude-sonnet-4"
}
```

Reject unknown models at PATCH.

## Audit (M09)

```json
{ "event_type": "agent.message_sent", "project_id", "run_id" }
{ "event_type": "agent.run_cancelled", "run_id" }
{ "event_type": "agent.session_reset", "old_session_id", "new_session_id" }
{ "event_type": "agent.attachment_uploaded", "filename", "size_bytes" }
```

No message body in audit by default (PII). Optional compliance mode: hash only.

## RBAC

| action | roles |
| --- | --- |
| chat | cabinet.operator+ |
| view others' sessions | cabinet.admin |
| reset own | cabinet.operator |
| reset any | cabinet.admin |
| upload | cabinet.operator |

## Prompt injection via inbox

- AGENTS.md instructs: prices only from tools
- Inbox files labeled untrusted in system prompt
- No auto-execution of macros in xlsx (extract tables only)
