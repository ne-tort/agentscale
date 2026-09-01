# Agent providers — analysis & verdicts

## Вопрос

Можно ли обернуть Codex / Claude Code **как Cursor SDK** (программный agent loop: create/resume/stream/tools), или только CLI / сырой LLM API?

## Scope

| В scope | Вне scope (пока) |
|---------|------------------|
| Cursor, Codex, Claude Agent SDK | **GLM / Z.ai** |
| **Platform OpenClaw** — наш universal runtime ([spec](../../06-agent-runtime/platform-openclaw-runtime.md)) | **Upstream** [openclaw/openclaw](https://github.com/openclaw/openclaw) as dependency |

HTTP LLM endpoints (OpenRouter, Ollama, custom) обслуживаются через **Platform OpenClaw**, не через отдельный «thin LLM extension».

## Краткие вердикты

| Provider | Agent SDK (как Cursor)? | CLI | Рекомендация Prodavan |
|----------|-------------------------|-----|------------------------|
| **Cursor** | **Да** — `@cursor/sdk` | IDE/cloud | **Primary** (уже в bot) |
| **Codex** | **Да** — Codex SDK (TS + Python) | `@openai/codex` | **Secondary** через SDK |
| **Claude Code** | **Да** — Claude Agent SDK | `@anthropic-ai/claude-code` | **Alternative** через Agent SDK + API key |
| **Platform OpenClaw** | **Да** — наш agent loop + HTTP LLM | — | **Universal** peer ([spec](../../06-agent-runtime/platform-openclaw-runtime.md)) |
| **Upstream OpenClaw** | Не dependency | openclaw/openclaw | **Не деплоить**; только идеи |

Подробности: [verdict-cursor](verdict-cursor.md) · [verdict-codex](verdict-codex.md) · [verdict-claude](verdict-claude.md).

---

## Что есть в репозитории сегодня

| Слой | Реальность |
|------|------------|
| Commerce Telegram bot | Боевой путь: **`@cursor/sdk`** (`cursor-claw` / `CursorSdkRuntime`) |
| Prodavan `platform_agent` | Stub (echo), без LLM |
| Legacy spikes | Codex CLI / Claude CLI subprocess в `docs/06-agent-runtime/*` (устарело относительно SDK-вердиктов ниже) |
| OpenClaw (upstream) | Не используется — **Platform OpenClaw** в разработке ([spec](../../06-agent-runtime/platform-openclaw-runtime.md)) |

---

## Смысл для архитектуры адаптеров

```text
AgentProviderPort
  ├─ CursorSdkAdapter           ← proprietary SDK
  ├─ CodexSdkAdapter            ← proprietary SDK
  ├─ ClaudeAgentSdkAdapter      ← proprietary SDK
  └─ PlatformOpenClawAdapter    ← universal (ai.http_providers + agent loop)
```

CLI subprocess остаётся **fallback** (CI, отладка), не канон для multi-tenant SaaS.

## Auth и ToS (сжато)

| Provider | SaaS-безопасный auth | Опасно для SaaS |
|----------|----------------------|-----------------|
| Cursor | Platform / company API key (`cursor_sdk`) | — |
| Codex SDK | OpenAI **API key** (pay-as-you-go) | Путать ChatGPT Pro limits с SDK |
| Claude Agent SDK | Anthropic **API key** | Раздавать claude.ai login / personal Max подписку тенантам (запрещено ToS) |

`cli_subscription` в AiProviderKey — только учёт биллинга, не runtime (см. [02 domain](../02-ai-provider-keys/domain.md)).

Ключи хранить в [02-ai-provider-keys](../02-ai-provider-keys/) с полем `api_kind`.
