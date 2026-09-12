# Reference UI — chat patterns

Upstream clones for UX research only. **Not** vendored into prodavan builds.

## Clone (local, not committed)

```bash
mkdir -p reference-ui
cd reference-ui

git clone --depth 1 https://github.com/danny-avila/LibreChat.git
git clone --depth 1 https://github.com/open-webui/open-webui.git
```

Add `reference-ui/*/` to `.gitignore` if clones are kept locally.

## Patterns to borrow

| Pattern | LibreChat | Open WebUI | Prodavan target |
|---------|-----------|------------|-----------------|
| SSE streaming bubbles | yes | yes | `POST /chat/stream` + transcript reload; **append-only** live deltas (no client overlap merge) |
| Tool call cards | yes | yes | `role=tool` + sidechain transcript API; close streaming text segment on `tool_call` |
| HITL approve modal | partial | yes | `ToolApprovePage` + pending-approvals |
| Session list / fork | yes | yes | `GET /agent/sessions`, `POST .../fork` |
| Attachments | yes | yes | `attachment_refs` on send |

## Prodavan implementation

- Flutter: `ProjectWorkspacePage`, `ProjectChatController` (`apps/flutter/lib/features/employee/`).
- API: `AgentSession` events → `events_to_transcript()`; sidechain via bridge HTTP.

Do not copy upstream auth, routing, or LLM provider stacks — platform keys and policy stay in Prodavan API.
