# Cabinet Runtime contract

Бывший «module contract» code-pack’ов. Теперь контракт **платформенного Cabinet Runtime** ↔ UI/Agent.

## Surface

| Method / route group | Назначение |
|----------------------|------------|
| Instance CRUD | create from Base / import, rename, archive |
| Meta CRUD | tables, columns, tabs, views |
| Data | rows query/upsert/delete |
| MCP registry | register/list/disable declarative tools |
| Bundle | export/import |
| Materialize hook | project prepare → mcp.json + workspace seeds |

Эквивалент SPI: всё, что раньше уходило в pack `execute_command`, теперь — **стабильные** `cabinet.*` операции Runtime.

## Import ban (обновлённый)

- Platform routers не содержат доменной логики «закупок».  
- Agent не получает raw SQL.  
- Custom executable MCP — не в v1.

## Events

| Event | Действие |
|-------|----------|
| `cabinet.meta.changed` | UI invalidate (v2 push) |
| `project.created` | materialize from cabinet |
| `cabinet.imported` | audit |

См. [dynamic-cabinets.md](dynamic-cabinets.md), [mcp-contracts.md](mcp-contracts.md).
