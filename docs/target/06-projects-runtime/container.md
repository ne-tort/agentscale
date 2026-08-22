# Project container

## Семантика

Изолированное FS/runtime пространство проекта. Default: **per-project pod**; допустим `local-ws:{workspace_key}` до k8s cutover.

Агент видит workspace + MCP: platform `cabinet.*` (scoped) + **enabled MCP packages** кабинета.

## Lifecycle

| Действие | Эффект |
|----------|--------|
| create | Volume + materialize + `container_ref` |
| update context | Re-materialize after prompts/MCP package changes |
| pause | Stop agent; keep volume |
| resume | Start; resume agent if supported |
| delete | Destroy + cabinet event |

## Откуда содержимое

```text
CabinetInstance meta/data/settings
  + mcp_packages (enabled)
  --materialize-->  /workspace FS + sandbox MCP processes
```

Источник истины — кабинет (UI/DB/packages), не git code-pack.

## Layout

```text
/workspace/
  AGENTS.md
  CLAUDE.md              # optional dual-write
  prompts/ rules/ skills/
  mcp.json               # platform + package wiring
  packages/              # extracted MCP package trees (sandbox roots)
  inbox/ out/
  cabinet-seed/
```

См. [workspace-context](../08-agent-providers/workspace-context.md), [mcp-packages](../05-cabinets/mcp-packages.md).

## Agent

[AgentProviderPort](../08-agent-providers/adapter-port.md); cwd=/workspace; key from AiProviderKey resolve.
