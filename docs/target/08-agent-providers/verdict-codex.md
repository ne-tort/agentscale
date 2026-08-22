# Вердикт: OpenAI Codex

## Итог

| Вопрос | Ответ |
|--------|-------|
| Есть ли SDK как у Cursor? | **Да** — официальный **Codex SDK** (TypeScript + Python) |
| Только CLI? | Нет: CLI **и** SDK |
| Можно ли обернуть в `AgentProviderPort`? | **Да**, предпочтительно через SDK, не через сырой `codex exec` |

## Что есть у OpenAI

| Поверхность | Назначение |
|-------------|------------|
| **Codex CLI** (`@openai/codex`) | Интерактивный терминальный агент; headless `codex exec --json` |
| **Codex SDK** | Программный контроль локальных Codex threads (CI, embed в приложение) — [developers.openai.com/codex/sdk](https://developers.openai.com/codex/sdk) |
| **App Server** | JSON-RPC для rich clients (IDE); Python SDK говорит с app-server |
| Chat/Responses API | Обычные LLM-вызовы **без** встроенного coding-agent harness |

SDK позиционируется именно для: CI/CD, своих агентов, встраивания Codex в tools/apps — то есть аналог обёртки Cursor SDK по смыслу (локальный coding agent под контролем процесса).

## Auth / billing (важно)

| Режим | Типично |
|-------|---------|
| Codex SDK | **API key**, pay-as-you-go (не «бесплатные» лимиты ChatGPT Pro) |
| Codex CLI | ChatGPT subscription **или** API key |

Для SaaS Prodavan: хранить `openai_api` / отдельный `codex_sdk` kind в AiProviderKey; не рассчитывать на личные Pro-подписки сотрудников.

## Рекомендация Prodavan

| Роль | Secondary / fallback |
|------|----------------------|
| Предпочтительный путь | `CodexSdkAdapter` (TS рядом с cursor bridge **или** Python `openai-codex`) |
| Fallback | `CodexCliAdapter` (`codex exec --json`) — как в legacy spike |
| `api_kind` | `codex_sdk` (новый) и/или `openai_api` |
| `provider` | `codex` |

Глубокий разбор: [capabilities-matrix](capabilities-matrix.md) · [permissions-policy](permissions-policy.md) · [vendor-docs/codex](vendor-docs/codex/).

## Риски

- SDK тянет/пинит runtime Codex CLI — образ worker должен это учитывать.
- Поведение sandbox/MCP отличается от Cursor — маппинг событий в единый `AgentEvent` обязателен.
- Legacy docs описывали только CLI spike — **обновить ожидания**: SDK существует и предпочтителен.
- Контекст проекта: Codex нативно читает **AGENTS.md** + skills + MCP — см. [workspace-context.md](workspace-context.md).
