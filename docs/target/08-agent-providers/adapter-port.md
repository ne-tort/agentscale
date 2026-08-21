# AgentProviderPort

Целевой порт coding-agent backends. GLM / OpenClaw / personal CLI subscription — **вне runtime**.

## Интерфейс

```text
AgentProviderPort
  create(opts: CreateOpts) -> AgentHandle
  resume(agentId, opts) -> AgentHandle
  send(handle, message: ChatMessage) -> Stream[AgentEvent]
  cancel(handle)
  close(handle)
```

### CreateOpts

| Field | Описание |
|-------|----------|
| `cwd` | Materialized workspace path |
| `model` | Model id (optional) |
| `mcpServers` | Из `materialize_project` |
| `apiKey` | Из AiProviderKey resolve (**не** cli_subscription) |
| `apiKind` | Runtime kind (`cursor_sdk`, …) |
| `sandbox` | Policy |

### ChatMessage

| Field | Описание |
|-------|----------|
| `text` | User text |
| `attachment_refs` | Ids из attachments pipeline |

## AgentEvent schema (frozen)

| `type` | `data` (минимум) | UI |
|--------|------------------|-----|
| `text_delta` | `{ text: string }` | Stream в bubble |
| `tool_call` | `{ id, name, input }` | Collapsed disclosure |
| `tool_result` | `{ id, name, output, is_error? }` | Disclosure |
| `usage` | `{ input_tokens?, output_tokens?, provider }` | Metrics → Admin |
| `error` | `{ code, message }` | Inline / snack |
| `done` | `{ reason?: string }` | Finalize turn |

Каждый event: опционально `at` (ISO timestamp).  
Адаптеры **обязаны** эмитить `usage` когда провайдер отдаёт counts (иначе Admin metrics пустые).

## Адаптеры

| Class | Статус |
|-------|--------|
| `CursorSdkAdapter` | Primary — **Node sidecar in-pod** (эталон Commerce `CursorSdkRuntime`, без Telegram) |
| `CodexSdkAdapter` | Secondary |
| `ClaudeAgentSdkAdapter` | Alternative (API key only) |
| CLI adapters | Spike/dev only, не multi-tenant SaaS |
| OpenAI-compatible | Extension LLM, не peer Cursor |

Sessions: **persist** в platform DB (не in-memory как канон).

## Где живёт

Bridge рядом с project workspace / container; API `/projects/{id}/agent/...`.  
Telegram bot = optional **trigger transport**, не owner сессий.
