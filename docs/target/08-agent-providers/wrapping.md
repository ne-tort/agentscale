# Wrapping SDKs → AgentProviderPort

Как обернуть **проприетарные SDK** и **Platform OpenClaw** в единый порт. Транспорт: **Node sidecar in project pod** (эталон Commerce bot без Telegram).

## Целевой порт

См. [adapter-port.md](adapter-port.md). Расширение CreateOpts для Admin policy:

```text
CreateOpts
  cwd, model, mcpServers, apiKey, apiKind
  toolPolicy: AgentToolPolicy      # from Admin / company override
  modelPolicy: ModelAllowlist      # optional restrict
  budget: { maxUsd?, maxTokens? }  # soft/hard stops
  settingSources: ["project"]      # NEVER "user"/"all" in SaaS pods
```

## Cursor (`CursorSdkAdapter`)

| Порт | SDK |
|------|-----|
| create | `Agent.create({ apiKey, model, local: { cwd, settingSources, sandboxOptions }, mcpServers })` |
| resume | `Agent.resume(id, { apiKey, model, local, mcpServers })` — **MCP снова** |
| send | `agent.send(text \| { text, images })` |
| stream | Map `assistant` / `tool_call` / `thinking` / `usage` → `AgentEvent` |
| cancel | `run.cancel()` if `supports("cancel")` |
| close | `asyncDispose` |
| usage | Stream `usage` + periodic/final `agent.getUsage()` for $ |

**Обязательные Prodavan defaults:**

- `local.settingSources: ["project"]` only (как Commerce; не `"all"`).
- `sandboxOptions.enabled` из `toolPolicy.sandbox` (Admin default: **true** в multi-tenant).
- Network: materialize `.cursor/sandbox.json` allowlist из policy.
- Model: из allowlist; fallback id согласовать (`composer-2.5` / catalog); не слать `"auto"` если SDK отвечает ConfigurationError — использовать catalog / `default` только если list подтверждает.

**Эталон кода:** `commerce/bot/src/core/orchestrator/cursorSdkRuntime.ts`.

## Codex (`CodexSdkAdapter`)

| Порт | SDK |
|------|-----|
| create | `new Codex({ apiKey/env, config })` + `startThread({ model, sandbox, … })` |
| resume | `resumeThread(threadId)` |
| send | `thread.run(prompt)` / streaming turn APIs |
| stream | Map JSONL/SDK events → `AgentEvent` |
| permissions | Map `AgentToolPolicy` → `sandbox_mode` / permission profile + `approval_policy` |

**Prodavan defaults:**

- `sandbox`: `workspace-write` или `read-only` per policy; never `danger-full-access` unless Admin explicit break-glass.
- `approval_policy`: в headless SaaS **`never` + узкий sandbox** ИЛИ bridge `on-request` → Prodavan approval page (как Claude `canUseTool`).
- Env: передавать минимальный `env` (не наследовать host secrets).

## Claude Agent SDK (`ClaudeAgentSdkAdapter`)

| Порт | SDK |
|------|-----|
| create/send | `query({ prompt, options })` with cwd, model, mcpServers, permissionMode, allowedTools, disallowedTools, canUseTool, settingSources, maxBudgetUsd |
| resume | session id / resume options |
| stream | assistant / tool / result messages → `AgentEvent` |
| usage | per-step usage + result `total_cost_usd` (**estimate**) |

**Prodavan defaults:**

- `settingSources: ["project"]` only.
- Dual-write `CLAUDE.md` at materialize ([workspace-context](workspace-context.md)).
- Map policy → `permissionMode` + tool allow/deny lists.
- `canUseTool` → platform approval channel (Employee UI page), never silent bypass in production.
- Branding: UI «Claude Agent», не «Claude Code».

## Platform OpenClaw (`PlatformOpenClawAdapter`)

Универсальный runtime — **не** vendor SDK. Спека: [platform-openclaw-runtime.md](../../06-agent-runtime/platform-openclaw-runtime.md).

| Порт | Реализация |
|------|------------|
| create | Session + LLM client из `ai.http_providers` entry (base_url, auth, dialect) |
| send | Agent loop: LLM ↔ tool calls (MCP + platform tools) |
| stream | Map loop events → `AgentEvent` |
| cancel | Abort in-flight turn |
| close | Drop session |

**Prodavan defaults:**

- Ingress **только** от platform API (нет messaging channels).
- `settingSources: ["project"]`; cwd = `/workspace`.
- Tool policy / HITL — те же, что у SDK-адапterов.
- Upstream `openclaw/openclaw` **не** импортировать — свой код в `agent-bridge`.

## Stream normalization

Все адаптеры → одни `AgentEvent` types. Tool names в UI показывать **канонические** (`fs.write`, `shell.exec`, `mcp.<server>.<tool>`), внутри адаптера маппить vendor ids.

## Failure model

| Класс | Cursor | Общий порт |
|-------|--------|------------|
| Не стартовал | `CursorAgentError` | `error` event + `retryable` |
| Упал mid-run | `result.status == "error"` | `error` + `done` |
| Cancel | cancelled status | `done{reason:cancelled}` |

## Sidecar layout

```text
project pod
  /workspace/          # materialized
  agent-bridge/        # Node: SDK adapters + Platform OpenClaw + HTTP to platform API
```

Python API оркестрирует; тяжёлые SDK (Cursor/Codex native bits) — в Node bridge.
