# Codex CLI — spike

Spike-адаптер **OpenAI Codex CLI** (`codex`) как fallback-провайдер `AgentProviderPort`. Статус: **эксперимент**, не для production tenant workloads.

---

## Цели spike

1. Проверить subprocess-based provider model
2. Dev/CI без Cursor API key
3. Benchmark latency vs Cursor SDK on identical prompts
4. Document gaps для production readiness

---

## Prerequisites

```bash
# Install Codex CLI (version pin in worker Dockerfile)
npm install -g @openai/codex@0.XX.X

# Auth
export OPENAI_API_KEY=sk-...
codex auth status
```

Worker image layer:

```dockerfile
RUN npm install -g @openai/codex@0.XX.X
ENV CODEX_CLI_PATH=/usr/local/bin/codex
```

---

## Adapter design

```python
class CodexCliAdapter(AgentProviderPort):
    async def create_session(...) -> CodexProviderSession:
        # No persistent session — workspace + env stored
        return CodexProviderSession(
            workspace_path=workspace_path,
            model=model,
            env=self._build_env(mcp_servers),
        )

class CodexProviderSession:
    async def send(self, text: str, ...) -> CodexProviderRun:
        proc = await asyncio.create_subprocess_exec(
            "codex", "exec",
            "--json",
            "--cwd", self.workspace_path,
            "--model", self.model,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
        )
        await proc.stdin.write(wrap_user_prompt(text).encode())
        await proc.stdin.drain()
        return CodexProviderRun(proc)
```

---

## Stream parsing

Codex `--json` emits JSONL on stdout:

```json
{"type":"message","role":"assistant","content":"..."}
{"type":"tool_call","name":"shell","status":"running"}
{"type":"tool_call","name":"shell","status":"completed","output":"..."}
```

Mapper → Prodavan `StreamEvent`:

| Codex JSON type | StreamEvent |
|-----------------|-------------|
| `message` delta | `assistant.delta` |
| `tool_call` start | `tool_call.started` |
| `tool_call` complete | `tool_call.completed` |
| `error` | `error` |

---

## MCP integration (wrapper)

Codex CLI **не** имеет native MCP. Spike options:

### Option A: MCP Gateway HTTP shim

Custom tool definitions in prompt → agent calls HTTP endpoint → gateway proxies MCP.

**Rejected for prod** — fragile, prompt injection risk.

### Option B: Pre-tool shell scripts

Expose `tools/run_pipeline.sh` in workspace that calls MCP via CLI.

**Spike only** — limited tool surface.

### Option C: Disable MCP

Codex spike runs with file-only tools (read/write workspace).

**Default for spike tests.**

---

## Session model

| Operation | Codex CLI |
|-----------|-----------|
| create_session | Setup workspace only |
| resume_session | **Same as create** — no resume |
| send | New `codex exec` per message |
| cancel | SIGTERM subprocess |
| dispose | Kill orphan processes |

Implication: **no conversation memory** between sends unless Codex maintains cwd state files.

---

## Security

Relies entirely on [worker-isolation.md](worker-isolation.md):
- K8s pod FS sandbox
- NetworkPolicy deny-by-default
- Non-root user

Codex CLI internal sandbox — **не доверяем**; treat as untrusted subprocess.

---

## Test plan

| # | Test | Pass criteria |
|---|------|---------------|
| C1 | `codex exec "list inbox files"` | stdout JSONL parsed |
| C2 | Cancel mid-run | SIGTERM → cancelled status |
| C3 | Escape path `../../etc/passwd` | Blocked by sandbox |
| C4 | 10 concurrent subprocesses | No zombie processes |
| C5 | Compare output quality vs Cursor | Manual review 5 prompts |

---

## Known gaps (prod blockers)

- ❌ No native MCP
- ❌ No session resume
- ❌ API billing per token — tenant cost allocation needed
- ❌ Subprocess overhead ~2-5s cold start
- ❌ Error handling immature vs SDK

---

## Enable spike

```bash
AGENT_PROVIDER=codex-cli
OPENAI_API_KEY=sk-...
FEATURE_CODEX_PROVIDER=true  # tenant feature flag
```

UI: model picker hides Codex unless flag enabled.

---

## Exit criteria

Spike **graduates** if:
- [ ] MCP bridge design approved (Option A rejected)
- [ ] p99 latency < 2× Cursor SDK
- [ ] Contract test suite passes
- [ ] Legal review OpenAI ToS for SaaS resale

Otherwise: remain dev-only fallback.

---

## Связанные документы

- [providers.md](providers.md)
- [provider-port.md](provider-port.md)
- [claude-code-spike.md](claude-code-spike.md)
