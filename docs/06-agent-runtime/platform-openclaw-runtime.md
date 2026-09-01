# Platform OpenClaw — универсальный agent runtime

**Prodavan OpenClaw** — наш собственный agent runtime в Pod: agent loop, MCP/tools, sandbox/policy, параллельные сессии.  
Команды **только от платформы** (API, triggers, SSE) — без Telegram/WhatsApp/bindings и прочего «канального» мусора upstream.

> **Не путать с upstream:** репозиторий [openclaw/openclaw](https://github.com/openclaw/openclaw) **не** является зависимостью и **не** деплоится as-is. Берём проверенные идеи (gateway, session manager, tool loop), переписываем под SaaS Pod и `AgentProviderPort`.

## Два класса runtime

| Класс | Адаптеры | LLM backend | Когда выбирать |
|-------|----------|-------------|----------------|
| **Проприетарные SDK** | `CursorSdkAdapter`, `CodexSdkAdapter`, `ClaudeAgentSdkAdapter` | Cursor / OpenAI Codex / Anthropic Agent SDK | Нужен vendor coding harness «как в IDE» |
| **Platform OpenClaw** | `PlatformOpenClawAdapter` | Любой HTTP endpoint из каталога `ai.http_providers` (OpenAI-compatible, Anthropic Messages, OpenRouter, Ollama, custom) | Универсальный agent с нашими tools/policy; свой API без vendor SDK |

Оба класса — **peer** через один `AgentProviderPort` и одни `AgentEvent`.  
Параллельные сессии разных классов в одном Pod — через `SessionManager` (отдельный `session_id` на адаптер).

## Границы

| В scope | Вне scope |
|---------|-----------|
| HTTP-only ingress от Prodavan API | Upstream OpenClaw channels (Telegram, WhatsApp, …) |
| `ai.http_providers` + resolve ключей | GLM / Z.ai как отдельный продуктовый трек |
| MCP + platform tools (fs/shell по policy) | Personal CLI subscription как runtime credential |
| Multi-session в Pod | Импорт skills/marketplace upstream без ревью |

## Архитектура в Pod

```text
agent-bridge/                    # один процесс на Pod
  SessionManager
    session_id → { adapter_kind, handle, cwd }
  CursorSdkAdapter               # proprietary
  CodexSdkAdapter                # proprietary
  ClaudeAgentSdkAdapter          # proprietary
  PlatformOpenClawAdapter        # universal
      agent loop (tool calling)
      mcp + platform tools
      llm client ← ai.http_providers entry
```

Control plane (триггеры, очередь, HITL UI) живёт в **API**, не дублируется вторым «clawbot»-процессом в Pod.

## Контракт с платформой

- Ingress: только authenticated calls от API (`create` / `send` / `stream` / `cancel` / `close`) — тот же bridge HTTP, что и для SDK-адаптеров.
- `api_kind`: `platform_openclaw` для runtime resolve; endpoint и auth — из `AiProviderKey` + catalog payload (`base_url`, paths, `openai_compatible`, …).
- Events: те же normalized `AgentEvent` ([adapter-port](../target/08-agent-providers/adapter-port.md)).
- Policy: `AgentToolPolicy`, budget, HITL approve — как у SDK-адапterов.

## Отличия от «extension LLM»

Раньше в каноне фигурировал «тонкий OpenAI-compatible adapter» без полноценного agent loop.  
**Platform OpenClaw** — полноценный peer: свой loop, те же tools/MCP, не chat-completions wrapper.

## База для реализации (референсы, не fork prod)

Spike и паттерны — из минимальных runtime (MicroClaw, Sandstorm API shape, agent-backplane sidecar IR).  
Код в prodavan — **свой**, под `AgentProviderPort`.

## Roadmap

| Phase | Deliverable |
|-------|-------------|
| P0 | ADR + bridge HTTP contract + `PlatformOpenClawAdapter` stub |
| P1 | OpenAI-compatible loop + MCP + один catalog preset (OpenRouter/Ollama) |
| P2 | Parallel sessions SDK + Platform OpenClaw; HITL bridge |
| P3 | Anthropic Messages dialect; performance / pool tuning |

См. также: [wrapping.md](../target/08-agent-providers/wrapping.md) · [02-ai-provider-keys domain](../target/02-ai-provider-keys/domain.md) · [PRODUCT.md](../PRODUCT.md).
