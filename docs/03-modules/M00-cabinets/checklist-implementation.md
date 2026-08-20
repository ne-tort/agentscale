# M00 — Checklist: implementation

## Реестр профилей и capabilities

- [ ] Таблица `cabinet_profiles` + seed данных минимум 2 профиля
- [ ] JSON Schema capabilities валидируется при регистрации профиля
- [ ] `electronics-procurement` включает s4b; `generic-assistant` — нет
- [ ] Unit: schema validation rejects s4b on non-electronics

## CRUD cabinets

- [ ] POST /v1/cabinets с slug validation `[a-z0-9-]{3,64}`
- [ ] GET list с pagination cursor
- [ ] PATCH только display_name + metadata
- [ ] DELETE soft → archived
- [ ] POST restore

## Pack seed pipeline

- [ ] Таблица `cabinet_seed_runs` с step tracking
- [ ] Idempotent re-run по `(cid, pack_version)`
- [ ] Rollback on failure (delete draft storage + row)
- [ ] `.seed-complete` marker
- [ ] Integration test: full seed electronics pack

## Cabinet switch API

- [ ] POST /v1/cabinets/{cid}/switch
- [ ] Update `cabinet_sessions` + JWT claims
- [ ] Invalidate Redis capability cache
- [ ] Emit `cabinet.switched` event
- [ ] Filter MCP servers post-switch

## Storage

- [ ] Prefix `storage/cabinets/{tid}/{cid}/`
- [ ] `.cabinet.json` written atomically
- [ ] Sandbox rules in agent runtime

## Persistence

- [ ] RLS on `cabinets`
- [ ] Trigger `enforce_s4b_profile`
- [ ] Audit log on all mutations

## MCP

- [ ] Server `prodavan-cabinets` with 4 tools
- [ ] Capability-based MCP registration

## Cross-cabinet negative tests

- [ ] NEG-CAB-001 … 010 automated
- [ ] NEG-CAB-ST-001 … 004
- [ ] NEG-CAB-MCP-001 … 004
- [ ] NEG-CAB-SEC-001 … 004

## Documentation

- [ ] OpenAPI spec published
- [ ] Runbook: failed seed recovery
