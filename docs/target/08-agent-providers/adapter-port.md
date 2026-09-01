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

## AgentEvent schema (frozen)

| `type` | `data` (минимум) | UI |
|--------|------------------|-----|
| `text_delta` | `{ text: string }` | Stream в bubble |
| `tool_call` | `{ id, name, input }` | Collapsed disclosure |
| `tool_result` | `{ id, name, output, is_error? }` | Disclosure |
| `tool_approval_request` | `{ id, name, input }` | Full-page approve (HITL) |
| `usage` | `{ input_tokens?, output_tokens?, provider, model? }` | Metrics → Admin |
| `error` | `{ code, message, retryable? }` | Inline / snack |
| `done` | `{ reason?: string }` | Finalize turn |

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
