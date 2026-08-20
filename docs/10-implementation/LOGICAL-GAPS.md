# Logical gaps (next iteration)

Gaps found while re-checking cabinet-layer work against ADR-001 / Cabinet SPI.

| ID | Layer | Gap | Severity | Status / next step |
|----|-------|-----|----------|--------------------|
| G1 | SPI | Facades imported cabinet exception types | med | **done** — `CabinetDomainError` in `cabinets/spi`; Pipeline/Catalog inherit |
| G2 | Infra | `http_s4b` lived under platform infra | med | **done** — moved to `cabinets/electronics_procurement/integrations/http_s4b.py` |
| G3 | SPI | `POST /commands` / `GET /queries` not HTTP | med | **done** — `/cabinets/{id}/spi/commands|queries/{name}` (multipart still on facades) |
| G4 | Events | inbox upload did not emit `file.uploaded` | low | **done** — emit from specs facade after upload |
| G4b | Events | `project.created` | done | — |
| G5 | Agent | In-memory sessions; no LLM / Cursor SDK / MCP gateway | high | Persist sessions; connect MCP ACL to real tools |
| G6 | Flutter | Procurement widgets still in shell | med | **partial** — `features/procurement/…/procurement_actions_panel.dart`; full package later |
| G7 | DB | Cabinet DB is SQLite file; no Postgres schema-per-cabinet | low | Optional DSN column + Alembic-per-pack |
| G8 | Registry | Pack install/upgrade/signature not implemented | high | Registry API + version pins on cabinet row |
| G9 | Auth | S4B vault tenant-scoped vs cabinet-scoped capability | med | **done** — vault under `cabinets/{id}/vault/s4b.json` + legacy tenant read fallback |
| G10 | Trigger | PG trigger hard-codes `electronics-procurement` for S4B | med | Drop trigger; enforce via SPI/capabilities |
| G11 | CI | ubuntu-latest jobs die in ~3s (empty steps) | med | **mitigated** — Flutter/Schemas → `[self-hosted, linux, docker]`; github-hosted still broken |
| G12 | Argo | k3s kubeconfig needs sudo | med | Passwordless kubeconfig / apply when available |
| G13 | SPI HTTP | Binary commands blocked on SPI HTTP (`upload_*`) | low | Keep domain facades; or add multipart SPI later |
| G14 | Agent UI | Chat is stub echo until LLM wired | high | Same as G5 |
| G15 | Events | Modules no-op on `file.uploaded` / `project.created` | low | Wire auto-index / welcome hooks when needed |

**This pass:** G1–G4, G3 HTTP SPI, G6 partial extract, runner python3, CI self-hosted for Flutter/Schemas, agent chat UX (bubbles + auto-session), generic-cabinet empty-state copy.
