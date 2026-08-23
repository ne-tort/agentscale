# Project container

## Семантика

Изолированное runtime-пространство проекта. Default: **per-project pod**.

**Blobs / workspace files:** канон — **object store (MinIO / S3)** ([13-platform-infra](../13-platform-infra/)).  
**Сейчас:** AGENTS/mcp/inbox/package.zip через `ObjectStorageManager`; sandbox extract + **hydrate from zip** если дерево отсутствует; live mount из MinIO в pod — hole. `local-ws:` — transitional.

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
  --materialize-->  object store (workspace prefix)
  --mount/sync-->   /workspace в pod + sandbox MCP processes
```

Источник истины метаданных — кабинет (UI/DB/packages); blobs — object store. Не git code-pack; не локальный `data/storage` API.

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
