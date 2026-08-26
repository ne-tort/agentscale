# Project workspace

Изоляция compute — модуль **[14 Project Containers](../14-project-containers/)** (Pod).  
Здесь — **layout файлов** и materialize в object store.

## Файлы

SoT blobs: **MinIO** `projects/{workspace_key}/…`.  
В Pod: `/workspace` после hydrate при `ContainerRuntimePort.start`.

```text
Cabinet meta + enabled MCP packages
  --materialize-->  MinIO prefix
  --hydrate------>  /workspace в Pod
```

## Layout

```text
/workspace/
  AGENTS.md
  CLAUDE.md              # optional
  prompts/ rules/ skills/
  mcp.json
  packages/              # MCP package trees
  inbox/ out/
  cabinet-seed/
```

См. [workspace-context](../08-agent-providers/workspace-context.md), [mcp-packages](../05-cabinets/mcp-packages.md).

## Lifecycle файлов

| Project op | Blobs | Pod (модуль 14) |
|------------|-------|-----------------|
| create | materialize | start Pod + hydrate |
| pause | keep MinIO | **delete Pod** |
| resume | keep | **new Pod** + hydrate |
| delete | wipe prefix | delete Pod |

Агент: [AgentProviderPort](../08-agent-providers/adapter-port.md); cwd=`/workspace`; key из AiProviderKey resolve.
