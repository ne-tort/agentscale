# 05 — Cabinets

Кабинет — изолированная вертикаль (свой BE + FE + schema), подключаемая к платформе по строгим контрактам.

**Сейчас:** modular monolith (не microservice).  
**Границы:** как к будущему microservice — [packaging.md](packaging.md).

| Документ | Содержание |
|----------|------------|
| [packaging.md](packaging.md) | **Канон изоляции**, monolit→service, base copy DX |
| [module-contract.md](module-contract.md) | Frozen SPI + bans + checklist |
| [default-cabinets.md](default-cabinets.md) | Base `generic-assistant` + `equipment-procurement` |
| [backend.md](backend.md) | Registry, schema-per-cabinet, MCP, secrets |
| [frontend.md](frontend.md) | `lib/cabinets/<id>/`, UiModule, base surfaces |

См. [06-projects-runtime](../06-projects-runtime/).
