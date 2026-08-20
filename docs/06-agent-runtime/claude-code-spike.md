# Claude Code CLI — spike

Spike-адаптер **Anthropic Claude Code CLI** (`claude`) как альтернативный subprocess-провайдер. Критическое отличие: auth через **Anthropic subscription (Max/Pro)**, **не через Anthropic API key**.

---

## ⚠️ Ограничение использования

> **Claude Code CLI предназначен для интерактивной разработки подписчиком Anthropic.**
> Использование personal subscription как backend SaaS для tenant workloads **вероятно нарушает ToS**.
> Spike — только для **local dev** и **architecture validation**, не production.

Prodavan production path: **Cursor SDK** с platform `CURSOR_API_KEY` или enterprise API contract.

---

## Prerequisites

```bash
# Install Claude Code CLI
npm install -g @anthropic-ai/claude-code@X.Y.Z

# Auth — interactive (dev machine)
claude auth login
# Creates ~/.claude/ session — NOT API key

claude auth status
```

Worker container auth options (dev only):

| Method | Risk |
|--------|------|
| Mount `~/.claude` from host | Credential leak between tenants — **dev only** |
| Baked auth in image | Rotation nightmare — **forbidden** |
| Subscription token env | Undocumented — **unsupported** |

**Production recommendation:** do not deploy Claude Code CLI adapter.

---

## Invocation

```bash
claude -p "<prompt>" \
  --output-format stream-json \
  --cwd /workspace \
  --allowedTools Read,Write,Bash \
  --disallowedTools WebFetch
```

Non-interactive mode required for headless worker.

---

## Adapter sketch

```python
class ClaudeCodeCliAdapter(AgentProviderPort):
    async def create_session(...) -> ClaudeProviderSession:
        if not settings.claude_cli_allowed:
            raise ConfigurationError("claude-code-cli disabled in this environment")
        return ClaudeProviderSession(workspace_path, model, allowed_tools)

    # resume_session → new session (no stable provider id)
```

```python
class ClaudeProviderSession:
    async def send(self, text: str, ...) -> ClaudeProviderRun:
        proc = await asyncio.create_subprocess_exec(
            settings.claude_cli_path,
            "-p", wrap_user_prompt(text),
            "--output-format", "stream-json",
            "--cwd", self.workspace_path,
            ...
        )
        return ClaudeProviderRun(proc)
```

---

## Stream JSON format

```json
{"type":"assistant","message":{"content":[{"type":"text","text":"..."}]}}
{"type":"tool_use","name":"Read","input":{"file_path":"..."}}
{"type":"tool_result","content":"..."}
```

Mapper:

| Claude event | Prodavan StreamEvent |
|--------------|----------------------|
| assistant text delta | `assistant.delta` |
| tool_use | `tool_call.started` |
| tool_result | `tool_call.completed` |
| error | `error` |

---

## MCP gap

Claude Code CLI **не поддерживает MCP protocol**.

Spike: `--allowedTools` limited to filesystem + bash; pipeline tools via shell scripts in workspace (same as Codex spike Option C).

---

## Subscription vs API

| Aspect | Claude Code CLI | Anthropic API |
|--------|-----------------|---------------|
| Auth | Subscription login | `ANTHROPIC_API_KEY` |
| Billing | Flat monthly | Per token |
| Headless SaaS | **Questionable ToS** | Supported |
| MCP | No | Via separate integration |
| Prodavan prod | **No** | Future option via Messages API |

**Do not set `ANTHROPIC_API_KEY` in Claude Code CLI adapter** — different product surface.

---

## Dev workflow (WSL)

```bash
# wsl-dev.md environment
export AGENT_PROVIDER=claude-code-cli
export CLAUDE_CLI_PATH=/usr/local/bin/claude
export FEATURE_CLAUDE_CLI=true

# Personal auth on dev machine only
claude auth login
```

Single developer, single tenant — acceptable for spike.

---

## Test plan

| # | Test | Notes |
|---|------|-------|
| H1 | Parse stream-json | Unit test fixture |
| H2 | Read AGENTS.md from workspace | Integration |
| H3 | Bash tool blocked outside workspace | Security |
| H4 | ToS review document | Legal sign-off required for any prod use |

---

## Comparison with Codex spike

| | Codex CLI | Claude Code CLI |
|---|-----------|-----------------|
| Auth | API key (billable) | Subscription (personal) |
| SaaS viable | Maybe | **No** |
| Stream format | JSONL | stream-json |
| Tool control | `--json` flags | `--allowedTools` |

---

## Exit criteria

- [ ] Legal: Anthropic ToS written opinion on headless use
- [ ] If negative → **archive adapter**, document API path instead
- [ ] If positive (unlikely for subscription) → enterprise agreement required

Default outcome: spike informs **Messages API adapter** design, CLI adapter deprecated.

---

## Связанные документы

- [providers.md](providers.md)
- [codex-cli-spike.md](codex-cli-spike.md)
- [worker-isolation.md](worker-isolation.md)
- [../07-infrastructure/wsl-dev.md](../07-infrastructure/wsl-dev.md)
