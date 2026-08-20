# Logical gaps (next iteration)

Gaps found while re-checking cabinet-layer work against ADR-001 / Cabinet SPI.

| ID | Layer | Gap | Severity | Next step |
|----|-------|-----|----------|-----------|
| G1 | SPI | Facades still import cabinet **exception types** (`PipelineError`, `CatalogError`) | med | Shared `CabinetDomainError` in `cabinets/spi` |
| G2 | Infra | `infrastructure/integrations/http_s4b.py` imports electronics parse helpers | med | Move HTTP S4B client under cabinet package |
| G3 | SPI | `POST /commands` / `GET /queries` not exposed as HTTP SPI yet (only in-process) | med | Mount OpenAPI SPI routes for remote modules |
| G4 | Events | inbox upload does not emit `file.uploaded` yet | low | Emit from pipeline upload_inbox |
| G4b | Events | `project.created` is emitted (best-effort) | done | — |
| G5 | Agent | In-memory sessions; no LLM / Cursor SDK / MCP gateway | high | Persist sessions; connect MCP ACL to real tools |
| G6 | Flutter | Procurement action widgets still live in shell (gated by tabs), not a separate Dart package | med | Extract `features/procurement/` module package |
| G7 | DB | Cabinet DB is SQLite file; no Postgres schema-per-cabinet yet | low | Optional DSN column + Alembic-per-pack runner |
| G8 | Registry | Pack install/upgrade/signature (docs Pack Registry) not implemented | high | Registry API + version pins on cabinet row |
| G9 | Auth | S4B vault is **tenant**-scoped while S4B capability is **cabinet**-scoped | med | Move vault key to cabinet_id |
| G10 | Trigger | PG trigger still hard-codes `electronics-procurement` for S4B | med | Drop trigger; enforce via SPI/capabilities only |
| G11 | CI | ubuntu-latest Flutter/Schemas jobs failed in ~3s (empty steps) — investigate runner/org | med | Re-run after push; check billing/permissions |
| G12 | Argo | k3s kubeconfig needs sudo; live Argo sync not verified | med | Document passwordless kubeconfig / apply when available |

**Done this pass:** SPI execute_command/query for electronics; facades dispatch via host; capabilities no longer enable catalogs for generic-assistant; runner image gets python3; CI bump uses python3|python fallback.
