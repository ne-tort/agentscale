# Workspace context — AGENTS / skills / rules / MCP

Как контекст проекта доходит до агента у разных провайдеров. Цель Prodavan: пользователь настраивает одно логическое содержимое в UI кабинета; `materialize_project` раскладывает файлы так, чтобы **каждый** выбранный runtime их подхватил.

## Логическая модель (платформа)

| Артефакт | Смысл | Кто правит |
|----------|--------|------------|
| **AGENTS** | Always-on инструкции проекта | UI кабинета → materialize → `AGENTS.md` |
| **Rules** | Узкие правила (часто path-scoped) | UI → `rules/` или vendor layout |
| **Skills** | On-demand процедуры (`SKILL.md`) | UI → skills tree |
| **Prompts** | Модульные фазы/сценарии кабинета | UI → `prompts/` (+ ссылки из AGENTS) |
| **MCP** | Внешние tools | UI → mcp config + allowlist из manifest |
| **Seed files** | Файлы в контейнере с create | UI / pack defaults → `cabinet-seed/` |

## Паритет провайдеров (research snapshot)

| Возможность | Cursor (`@cursor/sdk`) | Codex (SDK/CLI) | Claude Agent SDK |
|-------------|------------------------|-----------------|------------------|
| Always-on project instructions | `AGENTS.md` + `.cursor/rules` | **`AGENTS.md`** (официальный discovery по дереву) | **`CLAUDE.md`** + `.claude/rules/*.md` (нужен `settingSources` incl. `project`) |
| Skills (`SKILL.md`) | Cursor skills / `.cursor/skills` | **Да** — `.agents/skills/` (и user/plugin scopes) | **Да** — `.claude/skills/` при project settings |
| MCP | SDK `mcpServers` / project MCP | **Да** — config / MCP | **Да** — `mcpServers` и/или project `.mcp.json` |
| Один файл `AGENTS.md` без адаптации | Да | Да | **Частично** — часто делают `CLAUDE.md` с `@AGENTS.md` или копируют содержимое при materialize |

**Вывод:** идея «AGENTS + skills + rules + MCP в контейнере» **работает у всех трёх**, но **пути и имена файлов различаются**. Платформа не требует, чтобы vendor читал один и тот же path: materialize пишет **vendor layout** (или dual-write: `AGENTS.md` + `CLAUDE.md`).

## Канон materialize

1. Источник истины — **кабинетная БД / UI**, не только git pack.
2. В workspace всегда есть канонический `AGENTS.md` (текст из UI).
3. Если `agent_provider=claude_code` (или dual): также `CLAUDE.md` (полная копия или `@AGENTS.md` per Claude conventions).
4. Skills/rules раскладываются в layout выбранного провайдера (таблица выше); при смене провайдера проекта — re-materialize.
5. MCP передаётся и файлом в workspace, и/или полями `CreateOpts.mcpServers` адаптера ([adapter-port](adapter-port.md)).
6. Адаптеры **обязаны** включать project filesystem settings (для Claude — `settingSources: ["project"]`, без чужого user home в multi-tenant pod).

## Не смешивать

| Не делать | Почему |
|-----------|--------|
| Копировать `~/.cursor` / `~/.codex` / `~/.claude` хоста в tenant pod | Утечка и недетерминизм |
| Считать CLI subscription skills «общими» для SaaS | ToS / изоляция — только API key + project workspace |
| Хардкодить только Cursor paths в UI кабинета | Ломает Codex/Claude |

## Связь

- [container.md](../06-projects-runtime/container.md)
- [default-cabinets.md](../05-cabinets/default-cabinets.md)
- [adapter-port.md](adapter-port.md)
- Вердикты: [cursor](verdict-cursor.md) · [codex](verdict-codex.md) · [claude](verdict-claude.md)
