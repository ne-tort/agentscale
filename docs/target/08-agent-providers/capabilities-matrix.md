# Capabilities matrix — Cursor / Codex / Claude Agent SDK

Сводка «что есть» для Prodavan. Детали vendor: [vendor-docs/](vendor-docs/).  
Обёртка: [wrapping.md](wrapping.md) · Permissions: [permissions-policy.md](permissions-policy.md) · Models: [models-and-routing.md](models-and-routing.md) · Metrics: [usage-metrics.md](usage-metrics.md).

## Пакеты / runtime

| | Cursor | Codex | Claude Agent SDK |
|--|--------|-------|------------------|
| Primary package | `@cursor/sdk` (Node ≥22.13) | `@openai/codex-sdk` (TS; wraps CLI) / Python `openai-codex` | `@anthropic-ai/claude-agent-sdk` / `claude-agent-sdk` |
| Эталон в Commerce | `bot/.../cursorSdkRuntime.ts` | — | — |
| Prodavan adapter | `CursorSdkAdapter` (sidecar Node in-pod) | `CodexSdkAdapter` | `ClaudeAgentSdkAdapter` |
| Auth для SaaS | `CURSOR_API_KEY` (user/team SA) | OpenAI **API key** | Anthropic **API key** |
| Запрещено в multi-tenant | — | Личные ChatGPT Pro limits как runtime | claude.ai login / Max subscription |

## Agent loop

| Capability | Cursor | Codex | Claude |
|------------|--------|-------|--------|
| Create / resume session | `Agent.create` / `Agent.resume` | `startThread` / `resumeThread` | `query` + `resume` / sessions |
| Stream events | `run.stream()` | JSONL / SDK events | async iterator messages |
| Cancel | `run.cancel()` if `supports` | thread interrupt (CLI/SDK) | abort / interrupt APIs |
| Dispose | `await using` / `close` | context manager | end query / process lifecycle |
| Local cwd workspace | `local: { cwd }` | workspace / cwd | `cwd` + settingSources |
| Cloud / hosted VM | `cloud: { repos }` | Codex cloud (отдельный продукт) | Managed Agents ≠ Agent SDK |

## Tools / FS / shell

| | Cursor | Codex | Claude |
|--|--------|-------|--------|
| Read/write/edit files | Built-in agent tools | Via sandbox + agent | Built-in Read/Write/Edit/… |
| Shell / terminal | Yes | Sandboxed exec | `Bash` tool |
| Search (rg/glob) | Built-in (+ `@cursor/sdk-*` binaries) | Via agent | Glob/Grep |
| MCP | Inline `mcpServers` + `.cursor/mcp.json` | MCP in config / permissions | `mcpServers` + project MCP |
| Skills | Cursor skills / project | `.agents/skills` | `.claude/skills` |
| Project instructions | AGENTS.md + settingSources | AGENTS.md | CLAUDE.md + rules |

## Permissions / sandbox (кратко)

| | Cursor | Codex | Claude |
|--|--------|-------|--------|
| Default local risk | **Sandbox off** by default (full FS+network+shell) | Sandbox + approval policy (safer defaults) | Permission modes + allow/deny + `canUseTool` |
| Harden | `local.sandboxOptions.enabled: true` + `.cursor/sandbox.json` network allowlist; hooks `preToolUse` | `sandbox_mode` / permission profiles + `approval_policy` | `permissionMode`, `allowedTools`/`disallowedTools`, hooks, `dontAsk` |
| HITL approve | Limited in headless (no IDE prompt); use hooks/deny | `on-request` / untrusted → needs approval bridge | `canUseTool` callback → map to Prodavan UI page |
| Network | Denied when sandbox on unless allowlist | Off by default locally | Via Bash/MCP; control with deny rules |

## Models

| | Cursor | Codex | Claude |
|--|--------|-------|--------|
| List models | `Cursor.models.list()` | Config / API model ids (e.g. gpt-5.x) | Anthropic model ids in query options |
| Default note | `composer-2.5`; local **requires** model; `"default"` used in Commerce bot (not `"auto"` which can ConfigurationError) | Pass explicit model on thread | Explicit model id |
| Router / auto | `auto-smart` + `optimize_for` (Teams/Enterprise) | — | — |
| Per-run override | `send({ model })` sticky | Per-turn sandbox/model options | Per `query` options |

## Usage / cost

| | Cursor | Codex | Claude |
|--|--------|-------|--------|
| Live tokens | `run.usage` / stream `usage` event (`TokenUsage`) | Provider-dependent (track API usage) | Per-step `usage` on assistant messages |
| Billed $ | `agent.getUsage()` → tokens + **cost** | OpenAI usage APIs / dashboard | `total_cost_usd` = **estimate only**; authoritative = Usage Cost API / Console |
| Budget cap | Plan/team limits in Cursor dashboard | Org limits | `maxBudgetUsd` / `max_budget_usd` (client-side vs estimate) |

## Prodavan mapping (обязательное)

Каждый adapter **обязан**:

1. Нормализовать stream → `AgentEvent` ([adapter-port](adapter-port.md)).
2. Применять **AgentToolPolicy** из Admin ([permissions-policy](permissions-policy.md)).
3. Эмитить `usage` events + писать в platform metrics store.
4. Не грузить ambient user settings (`settingSources` / home configs) в multi-tenant pod — только project workspace.

## Исходники research

- Cursor skill: Cursor SDK skill + live docs snapshot  
- Commerce: `CursorSdkRuntime` (`settingSources: ["project"]`, `sandboxOptions`, MCP, model)  
- Vendor snapshots: [vendor-docs/](vendor-docs/)
