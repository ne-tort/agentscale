# Agent providers — analysis & verdicts

## Вопрос

Можно ли обернуть Codex / Claude Code **как Cursor SDK** (программный agent loop: create/resume/stream/tools), или только CLI / сырой LLM API?

## Scope

| В scope | Вне scope (пока) |
|---------|------------------|
| Cursor, Codex, Claude Agent SDK | **GLM / Z.ai**, произвольные LLM-only backends без первостороннего agent SDK |

OpenRouter как HTTP LLM — опциональный extension (`openrouter`), не peer Cursor/Codex/Claude.

## Краткие вердикты

| Provider | Agent SDK (как Cursor)? | CLI | Рекомендация Prodavan |
|----------|-------------------------|-----|------------------------|
| **Cursor** | **Да** — `@cursor/sdk` | IDE/cloud | **Primary** (уже в bot) |
| **Codex** | **Да** — Codex SDK (TS + Python) | `@openai/codex` | **Secondary** через SDK |
| **Claude Code** | **Да** — Claude Agent SDK | `@anthropic-ai/claude-code` | **Alternative** через Agent SDK + API key |
| **OpenClaw** | Не наш runtime | — | Не использовать |

Подробности: [verdict-cursor](verdict-cursor.md) · [verdict-codex](verdict-codex.md) · [verdict-claude](verdict-claude.md).

---

## Что есть в репозитории сегодня

| Слой | Реальность |
|------|------------|
| Commerce Telegram bot | Боевой путь: **`@cursor/sdk`** (`cursor-claw` / `CursorSdkRuntime`) |
| Prodavan `platform_agent` | Stub (echo), без LLM |
| Legacy spikes | Codex CLI / Claude CLI subprocess в `docs/06-agent-runtime/*` (устарело относительно SDK-вердиктов ниже) |
| OpenClaw | Только упоминания в legacy — **кода запуска нет** |

---

## Смысл для архитектуры адаптеров

```text
AgentProviderPort
  ├─ CursorSdkAdapter      ← SDK first-class
  ├─ CodexSdkAdapter       ← Codex SDK (не только `codex exec`)
  ├─ ClaudeAgentSdkAdapter ← Claude Agent SDK (не только `claude -p`)
  └─ OpenAiCompatibleLlmAdapter ← OpenRouter / сырой OpenAI (extension, не peer)
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
