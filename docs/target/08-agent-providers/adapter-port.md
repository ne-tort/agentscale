# AgentProviderPort

Целевой порт coding-agent backends. **Два класса:** проприетарные SDK + **Platform OpenClaw** (универсальный runtime).  
GLM / upstream OpenClaw / personal CLI subscription — **вне runtime**.

Детали по SDK: [capabilities-matrix](capabilities-matrix.md) · [wrapping](wrapping.md) · Platform OpenClaw: [platform-openclaw-runtime](../../06-agent-runtime/platform-openclaw-runtime.md) · [permissions-policy](permissions-policy.md).

## Интерфейс

```text
AgentProviderPort
  create(opts: CreateOpts) -> AgentHandle
  resume(agentId, opts) -> AgentHandle
  send(handle, message: ChatMessage) -> Stream[AgentEvent]
  cancel(handle)
  close(handle)
  getUsage?(handle) -> VendorUsage   # optional; Cursor getUsage etc.
```

### CreateOpts

| Field | Описание |
|-------|----------|
| `cwd` | Materialized workspace path |
| `model` | Model id (+ params) from allowlist |
| `mcpServers` | Из materialize ∩ policy |
| `apiKey` | AiProviderKey resolve (**не** cli_subscription) |
| `apiKind` | `cursor_sdk` / `codex_sdk` / `claude_agent_sdk` / `platform_openclaw` / … |
| `toolPolicy` | `AgentToolPolicy` ([permissions-policy](permissions-policy.md)) |
| `budget` | Optional `{ maxUsd?, maxTokens? }` |
| `settingSources` | SaaS default: `["project"]` only |

### ChatMessage

| Field | Описание |
|-------|----------|
| `text` | User text |
| `attachment_refs` | Ids из attachments pipeline |
| `images` | Optional inline images (Cursor send shape) |

## AgentEvent schema (frozen v1)

| `type` | `data` (минимум) | UI |
|--------|------------------|-----|
| `text_delta` | `{ text: string }` — **incremental** on platform wire after ingress; SDK may emit cumulative or partial-overlap chunks, normalized via [`text_delta.py`](../../apps/api/src/prodavan/application/agent/text_delta.py) (prefix + suffix/prefix overlap merge) | Stream inline в chat column |
| `tool_call` | `{ id, name, input }` | Collapsed disclosure |
| `tool_result` | `{ id, name, output, is_error? }` | Disclosure |
| `tool_approval_request` | `{ id, name, input }` | Full-page approve (HITL) |
| `usage` | `{ input_tokens?, output_tokens?, provider, model? }` | Metrics → Admin |
| `error` | `{ code, message, retryable? }` | Inline / snack |
| `done` | `{ reason?: string }` | Finalize turn |

## Extended stream types (v2 — persisted + SSE)

Platform API и bridge **forward + persist** (не отбрасывают):

| `type` | UI block |
|--------|----------|
| `system_init` | session debug header |
| `thinking_delta` / `thinking_complete` | collapsible reasoning |
| `tool_call_delta` / `tool_progress` | live tool args / spinner |
| `subagent_start` / `subagent_event` / `subagent_stop` | nested subagent card |
| `task_progress` | plan checklist |
| `status` / `compact_boundary` / `permission_denial` | system notices |

Transcript projection: [`chat_projection.py`](../../apps/api/src/prodavan/application/agent/chat_projection.py) `events_to_chat_blocks()` → `{ blocks: [...] }` для Flutter `core/chat`.

Каждый event: опционально `at` (ISO timestamp).  
Адаптеры **обязаны** эмитить `usage` когда провайдер отдаёт counts.

## Адаптеры

| Class | Статус |
|-------|--------|
| `CursorSdkAdapter` | Primary — Node sidecar (эталон Commerce `CursorSdkRuntime`) |
| `CodexSdkAdapter` | Secondary |
| `ClaudeAgentSdkAdapter` | Alternative (API key only) |
| `PlatformOpenClawAdapter` | Universal — agent loop + `ai.http_providers` ([spec](../../06-agent-runtime/platform-openclaw-runtime.md)) |
| CLI adapters | Spike/dev only |

Sessions: **persist** в platform DB.

## Где живёт

Bridge в project container; API `/projects/{id}/agent/...`.  
Telegram = optional trigger transport.
