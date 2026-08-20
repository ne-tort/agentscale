# M04 — Checklist: implementation

## User catalogs

- [ ] Upload xlsx/csv/sqlite
- [ ] Index job → catalog.sqlite
- [ ] manifest.json + status ready
- [ ] GET list/detail/archive
- [ ] trusted_seller flag for M02 rank
- [ ] Cabinet-scoped storage paths

## System databases

- [ ] Seed system_databases table with s4b-cache deletable=false
- [ ] GET system-databases filtered by profile
- [ ] DELETE returns 403 SYSTEM_DATABASE_NON_DELETABLE
- [ ] s4b-cache virtual MCP integration
- [ ] Generic cabinet: empty system list

## S4B credentials

- [ ] Vault write/read (encrypted)
- [ ] PUT credentials + validate
- [ ] State machine: missing/valid/invalid/rate_limited
- [ ] GET status with username_hint only
- [ ] DELETE → missing_credentials
- [ ] rate_limited sets rate_limit_reset_at

## Cache

- [ ] s4b_cache_entries table + TTL job
- [ ] in_stock only in cache writes
- [ ] Strip on_order at cache ingest

## MCP

- [ ] list_databases profile filter
- [ ] commerce-s4b registration gated
- [ ] Error codes for cred states

## Tests

- [ ] NEG-CAT-001 … 007
- [ ] INV-CAT-001 … 007
- [ ] Cross-module: M02 search skips s4b on generic
