# Project container

## Семантика

Изолированное FS/runtime пространство проекта. Default целевой: **per-project pod**; допустим `local-ws:{workspace_key}` до k8s cutover.

## Lifecycle

| Действие | Эффект |
|----------|--------|
| create | Volume/path + `materialize_project` + `container_ref` |
| pause | Stop agent; сохранить volume |
| resume | Start; resume agent если provider умеет |
| delete | Destroy + cabinet `project.deleted` event |

Idle / scale-to-zero — platform policy per company plan (документировать в ops позже; default: pause after idle TTL).

## Layout

```text
/workspace/
  AGENTS.md
  prompts/ rules/ skills/
  mcp.json
  inbox/ out/
  cabinet-seed/
```

## Agent

Adapter [AgentProviderPort](../08-agent-providers/adapter-port.md) с cwd=workspace и key из AiProviderKey resolve.  
Primary: Node sidecar рядом с workspace (не Telegram).
