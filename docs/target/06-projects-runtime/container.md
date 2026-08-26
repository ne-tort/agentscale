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
| pause | Cancel agent sessions + **pause container** (API: `pause_container`; keep volume; k8s pod stop — hole). Triggered by project pause only (incl. key cascade → `ProjectService.pause`), never key→container directly |
| resume | Manual; requires valid AI key (`resolve_credentials`); then start; resume agent if supported. Key re-enable does **not** auto-resume projects |
| delete | Soft-delete + wipe workspace + cabinet event |

См. также каскад ключей/компаний: [02 domain](../02-ai-provider-keys/domain.md), [01 domain](../01-platform-admin/domain.md).

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
