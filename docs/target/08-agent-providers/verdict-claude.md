# Вердикт: Claude Code / Claude Agent SDK

## Итог

| Вопрос | Ответ |
|--------|-------|
| Есть ли SDK как у Cursor? | **Да** — **Claude Agent SDK** («Claude Code as a library») |
| Только CLI? | Нет: CLI **и** Agent SDK |
| Можно ли обернуть? | **Да** через Agent SDK + **API key** |

## Что есть у Anthropic

| Поверхность | Пакеты / вход | Назначение |
|-------------|---------------|------------|
| **Claude Agent SDK** | TS: `@anthropic-ai/claude-agent-sdk`; Py: `claude-agent-sdk` | Тот же agent loop / tools / MCP / sessions, что Claude Code, в процессе приложения — [code.claude.com/docs/en/agent-sdk](https://code.claude.com/docs/en/agent-sdk/overview) |
| **Claude Code CLI** | `@anthropic-ai/claude-code` | Интерактив + headless `-p` / `--output-format json` |
| **Client SDK** | `@anthropic-ai/sdk` и т.п. | Сырой Messages API — **свой** tool-loop |
| **Managed Agents** | Hosted REST | Отдельный продукт Anthropic (не то же, что Agent SDK) |

Rename (2026): бывший «Claude Code SDK» → **Claude Agent SDK**. Не ставить `@anthropic-ai/claude-code` ожидая SDK symbols — это CLI.

## Auth / ToS (критично для SaaS)

| Режим | Вердикт |
|-------|---------|
| Agent SDK + **Anthropic API key** | **Допустимый** путь для Prodavan |
| Claude Code CLI + **личная Max/Pro** subscription | **Не для multi-tenant SaaS** (ToS / credential sharing) |
| Отдавать пользователям claude.ai login / rate limits чужого продукта | **Запрещено** Anthropic для third-party (явно в Agent SDK docs) |

Legacy spike (`claude-code-spike.md`) правильно предостерегал про subscription CLI; вывод target: **не CLI-subscription**, а **Agent SDK + API key**.

## Рекомендация Prodavan

| Роль | Alternative production path |
|------|----------------------------|
| Adapter | `ClaudeAgentSdkAdapter` |
| Fallback | CLI headless только для spike/dev |
| `provider` | `claude_code` |
| `api_kind` | `claude_agent_sdk` (предпочтительно) или `anthropic_api` (если свой loop) |
| Branding | Не называть продукт «Claude Code»; в UI — «Claude Agent» / свой бренд |

## Риски

- SDK бандлит native binary — pin + multi-arch worker images.
- Permissions/hooks модель другая, чем Cursor — нужен маппинг в `AgentEvent`.
- Стоимость API ≠ «безлимит» подписки Claude Max.
- Контекст: **CLAUDE.md** / `.claude/skills` / rules (не тот же path, что Cursor) — materialize dual-write, см. [workspace-context.md](workspace-context.md).
