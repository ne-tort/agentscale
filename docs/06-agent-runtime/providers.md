# Провайдеры агентов

> **LEGACY.** Канон и актуальный анализ: [docs/target/08-agent-providers/](../target/08-agent-providers/). Ключи провайдеров: [02-ai-provider-keys](../target/02-ai-provider-keys/).

Prodavan поддерживает несколько **backend-провайдеров** для выполнения LLM-сессий. Выбор провайдера — конфигурация deployment + per-tenant override; default — **Cursor SDK**.

---

## Сводная таблица

| Provider | Priority | Transport | Auth | Status |
|----------|----------|-----------|------|--------|
| **Cursor SDK** | Primary (prod) | `@cursor/sdk` npm / Python SDK | `CURSOR_API_KEY` | Production target |
| **Codex CLI** | Secondary / fallback | subprocess `codex` | OpenAI API key or OAuth | Spike |
| **Claude Code CLI** | Alternative | subprocess `claude` | **Anthropic subscription** (Max/Pro) | Spike — **не API** |

---

## Cursor SDK (primary)

### Почему primary

- Нативная интеграция с MCP tool loop
- Streaming events aligned с M07 UI
- Sandbox options (`sandbox.json`)
- Resume session / agent pool — проверено в Commerce MVP (`CursorSdkRuntime`)

### Requirements

```text
CURSOR_API_KEY=...          # platform secret
CURSOR_MODEL=claude-sonnet-4  # default model id
```

### Capabilities

| Feature | Support |
|---------|---------|
| Create / resume agent | ✓ |
| MCP servers injection | ✓ |
| Stream events | ✓ |
| Cancel run | ✓ |
| Multimodal (images) | ✓ |
| Cloud agents | optional v2 |

### Deployment

Worker pod image includes:
- Node.js runtime for SDK bridge **or** Python `cursor-sdk`
- `@cursor/sdk` pinned version

См. [cursor-sdk-adapter.md](cursor-sdk-adapter.md).

---

## Codex CLI (spike)

### Назначение

Fallback когда Cursor SDK недоступен; локальная разработка без Cursor subscription; CI smoke tests.

### Invocation

```bash
codex exec --json --cwd /workspace --model gpt-4.1 "<prompt>"
```

Subprocess managed by `CodexCliAdapter`.

### Auth

- `OPENAI_API_KEY` — API billing
- **Не путать** с Claude Code subscription

### Limitations (spike)

- MCP через external wrapper (не native)
- Stream parsing из JSONL stdout
- No resume — new process per message
- Sandbox — rely on K8s pod isolation only

См. [codex-cli-spike.md](codex-cli-spike.md).

---

## Claude Code CLI (spike)

### Назначение

Alternative для операторов с **Anthropic Claude Max/Pro subscription** — billing через подписку, **не через Anthropic API**.

### Invocation

```bash
claude -p "<prompt>" --output-format stream-json --cwd /workspace
```

### Auth

- Interactive login session (`claude auth`) baked into runner image **или**
- Subscription token from host mount (dev only)

### Critical constraint

> **Claude Code CLI использует subscription auth, не `ANTHROPIC_API_KEY`.**
> Prodavan **не** должен billing tenant через operator's personal subscription in prod.
> Spike — для dev/experiment; prod — Cursor SDK или enterprise API contract.

### Limitations

- Terms of service: subscription ≠ headless SaaS backend
- No official MCP — custom bridge required
- Session state opaque

См. [claude-code-spike.md](claude-code-spike.md).

---

## Provider selection

### Config hierarchy

```text
1. Tenant override (M08 admin) — if allowed by plan
2. Cabinet override (M00) — rare
3. Platform default: cursor-sdk
4. Dev .env: PROVIDER=codex-cli
```

### Environment variable

```bash
AGENT_PROVIDER=cursor-sdk   # cursor-sdk | codex-cli | claude-code-cli
```

Worker orchestrator reads at pod spawn → selects adapter implementing `AgentProviderPort`.

---

## Feature matrix

| Feature | Cursor SDK | Codex CLI | Claude Code CLI |
|---------|------------|-----------|-----------------|
| MCP native | ✓ | △ wrapper | ✗ |
| Resume session | ✓ | ✗ | △ |
| Cancel mid-run | ✓ | △ SIGTERM | △ |
| Stream to SSE | ✓ | ✓ JSONL | ✓ JSON |
| Multimodal | ✓ | △ | △ |
| Prod ready | ✓ | ✗ spike | ✗ spike |
| Cost model | API key platform | API key platform | Personal sub — dev only |

---

## Commerce MVP reference

Commerce (`cursor-claw` fork) implements only **Cursor SDK**:

| Commerce | Prodavan |
|----------|----------|
| `IAgentRuntime` | `AgentProviderPort` |
| `CursorSdkRuntime` | `CursorSdkAdapter` |
| `AgentOrchestrator.ts` | `AgentOrchestrator` (Python) |
| Telegram stream | SSE → Flutter |

---

## Roadmap

| Phase | Deliverable |
|-------|-------------|
| v0.1 | Cursor SDK adapter only |
| v0.2 | Codex CLI spike behind feature flag |
| v0.3 | Evaluate Claude Code ToS for SaaS |
| v1.0 | Multi-provider with tenant billing integration |

---

## Связанные документы

- [provider-port.md](provider-port.md)
- [cursor-sdk-adapter.md](cursor-sdk-adapter.md)
- [worker-isolation.md](worker-isolation.md)
- [../03-modules/M07-agent/](../03-modules/M07-agent/)
