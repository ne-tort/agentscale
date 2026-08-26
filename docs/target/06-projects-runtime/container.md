# Project container

> **Канон isolator:** [14 — Project Containers](../14-project-containers/).  
> Этот файл — layout workspace + связка Project ↔ blobs. End-state «container = только object-ws» **deprecated**.

## Семантика

Изолированное runtime-пространство проекта. Default target: **per-project pod** (модуль 14).

**As-is (transitional):** `container_ref = object-ws:{workspace_key}` — логический workspace в object store / local FS; `pause_container` — no-op; PVC probe Job — **не** runtime агента.

**Blobs / workspace files:** канон — **object store (MinIO / S3)** ([13-platform-infra](../13-platform-infra/)).  
**Сейчас:** AGENTS/mcp/inbox/package.zip через `ObjectStorageManager`; sandbox extract + **hydrate from zip** если дерево отсутствует; live mount из MinIO в pod — hole. `local-ws:` — transitional.

Агент видит workspace + MCP: platform `cabinet.*` (scoped) + **enabled MCP packages** кабинета.

## Lifecycle

Владелец k8s / pause–start–force-kill — **ContainerRuntimePort** ([14 lifecycle](../14-project-containers/lifecycle.md)). ProjectService вызывает Port; не AiKeys напрямую.

| Действие | Эффект |
|----------|--------|
| create | Materialize + ensure Container (`container_ref` opaque) |
| update context | Re-materialize after prompts/MCP package changes |
| pause | Cancel agent sessions + **Container.pause** (keep volume; pod stop — hole until P3). Triggered by project pause only (incl. key cascade → `ProjectService.pause`), never key→container directly |
| resume | Manual; requires valid AI key (`resolve_credentials`); then Container.start. Key re-enable does **not** auto-resume projects |
| delete | Soft-delete + wipe workspace + cabinet event + Container.delete |

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
