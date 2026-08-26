# 05 — Cabinets

**Кабинет** = абстрактный реестр + meta/data в своей PG schema. UI и домен — из метаданных.

Старт: **[entity.md](entity.md)** · иерархия: [00-entities](../00-entities.md).

| Документ | Содержание |
|----------|------------|
| [entity.md](entity.md) | Оболочка vs meta — канон сущности |
| [assignment.md](assignment.md) | Company назначает Employee ↔ Cabinet |
| [materialize-from-meta.md](materialize-from-meta.md) | Meta / MinIO → Pod `/workspace` |
| [dynamic-cabinets.md](dynamic-cabinets.md) | Patterns, isolation, MCP |
| [architecture.md](architecture.md) | Runtime vs instance |
| [meta-and-ui.md](meta-and-ui.md) | tables/tabs/views → Flutter |
| [mcp-packages.md](mcp-packages.md) | Custom MCP zip |
| [mcp-contracts.md](mcp-contracts.md) | Platform `cabinet.*` |
| [bundle-format.md](bundle-format.md) | Export/import |
| [default-cabinets.md](default-cabinets.md) | Base + starter bundles |
| [packaging.md](packaging.md) | Schema isolation |
| [module-contract.md](module-contract.md) | Runtime SPI |
| [backend.md](backend.md) / [frontend.md](frontend.md) | Persistence / shell |

Связано: [06 Projects](../06-projects-runtime/), [14 Containers](../14-project-containers/), [08 Agents](../08-agent-providers/).
