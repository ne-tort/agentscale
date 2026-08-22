# Project container

## Семантика

Изолированное FS/runtime пространство проекта. Default целевой: **per-project pod**; допустим `local-ws:{workspace_key}` до k8s cutover.

Агент в контейнере видит **только** этот workspace (+ разрешённые MCP). Контекст формируется кабинетом, не «общим диском компании».

## Lifecycle

| Действие | Эффект |
|----------|--------|
| create | Volume/path + `materialize_project` + `container_ref` |
| update context | Повторный materialize (идемпотентно) после смены промптов/MCP в UI кабинета |
| pause | Stop agent; сохранить volume |
| resume | Start; resume agent если provider умеет |
| delete | Destroy + cabinet `project.deleted` event |

Idle / scale-to-zero — platform policy per company plan (позже в ops; default: pause after idle TTL).

## Откуда берётся содержимое

```text
Cabinet DB / UI settings  --materialize_project-->  /workspace FS
     prompts, skills, rules, AGENTS fragments,
     MCP configs, seed files, pack defaults
```

Пользователь настраивает контекст в UI кабинета; при создании проекта (и по политике — при sync) SPI пишет файлы в контейнер. Pack git defaults — только **начальные** шаблоны, не единственный источник истины.

## Layout (логический канон)

```text
/workspace/
  AGENTS.md                 # always-on instructions (канон для Cursor/Codex)
  CLAUDE.md                 # optional alias/symlink/copy для Claude Agent SDK
  prompts/                  # модульные промпты кабинета
  rules/                    # или .cursor/rules / .claude/rules — см. workspace-context
  skills/                   # SKILL.md деревья (layout под провайдера при materialize)
  mcp.json                  # и/или провайдер-специфичный MCP config
  inbox/ out/
  cabinet-seed/             # файлы, которые должны быть в контейнере с первого дня
```

Точные пути skills/rules **нормализует materialize** под `agent_provider` проекта (см. [workspace-context](../08-agent-providers/workspace-context.md)).

## Agent

Adapter [AgentProviderPort](../08-agent-providers/adapter-port.md) с `cwd=/workspace` и key из AiProviderKey resolve.  
Primary: Node sidecar рядом с workspace (не Telegram).
