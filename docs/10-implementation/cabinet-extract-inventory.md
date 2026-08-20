# Inventory: extract into electronics-procurement cabinet

Maps current **core** code that must move behind Cabinet SPI (`electronics-procurement`).

## Must extract (domain)

| Path | Role | SPI surface |
|------|------|-------------|
| `application/pipeline/*` | ingest, classify, search, variants, kp_export, spec_table | commands `create_run`, `advance_run`, `finalize_run`, `export_kp`; queries `list_runs`, `describe_run`, `list_lineitems`, `list_offers` |
| `application/services/pipeline_service.py` | Orchestration | same |
| `application/catalogs/*` | indexer, search, s4b_search | commands `upload_catalog`, `s4b_search`; queries `list_catalogs` |
| `application/services/catalog_service.py` | Catalogs API | same |
| `application/integrations/*` | S4B port/runtime | commands `s4b_*` |
| `infrastructure/integrations/http_s4b.py` | HTTP client | infra of cabinet |
| `api/v1/specs.py` | HTTP | Platform facade → SPI |
| `api/v1/catalogs.py` | HTTP | Platform facade → SPI |
| Pack `electronics-procurement/**` | prompts, shops, theme | already pack-owned |
| Domain capability keys `procurement.*` | flags | stay as strings in profile; mapping may live in cabinet |

## Stays in Platform Core

| Path | Reason |
|------|--------|
| `api/v1/auth|me|admin|cabinets|projects|prompts|health` | Identity / registry / workspace / prompts framework |
| `application/services/{auth,cabinet,project,prompt,admin,pack_seeder}_service.py` | Platform |
| `infrastructure/persistence/models/*` | Platform DB only |
| `infrastructure/storage/*` | Workspace FS ports |
| `domain/capabilities.py` | Generic snapshot builder (cabinet-agnostic mapping preferred over time) |

## Cabinet DB (new)

| Artifact | Location |
|----------|----------|
| SQLite DSN | `{storage}/cabinets/{tenant}/{cabinet}/cabinet.sqlite` |
| Migrations | `packages/cabinet-packs/electronics-procurement/migrations/` |
| Runner | SPI `POST /migrate` |

MVP tables (electronics): optional `equipment_cards`, `run_index` — domain data that today is mostly FS/`commerce.sqlite` may remain on project FS; cabinet.sqlite holds module-local indexes.

## Import freeze check

After extract, `prodavan.api` and `prodavan.platform` must not import:

- `prodavan.application.pipeline`
- `prodavan.application.catalogs`
- `prodavan.application.integrations`

Allowed: `prodavan.cabinets.registry` → module adapters only.
