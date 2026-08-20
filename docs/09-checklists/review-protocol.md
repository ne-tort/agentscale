# Review protocol (per phase / iteration)

После каждой фазы P0–P9 и перед merge:

## 1. Semantics

- [ ] Сверка с Commerce [`AGENTS.md`](https://github.com/ne-tort/Commerce/blob/main/AGENTS.md) (цены только из tools, no kp.xlsx agent)
- [ ] Tenant → Cabinet → Project isolation documented
- [ ] S4B only electronics-procurement

## 2. Isolation

- [ ] RLS `tenant_id` + `cabinet_id` on all business tables
- [ ] Negative tests IDs documented (NEG-CAB, NEG-CAT, …)
- [ ] Agent worker cannot read PG directly

## 3. SOLID

- [ ] One module = one bounded context (M00–M09)
- [ ] Cabinet packs extend core without modifying core code paths
- [ ] MCP Gateway single responsibility

## 4. DRY

- [ ] NavGate/FeatureGate single mechanism (no per-page if electronics)
- [ ] Widget catalog — no page-local widgets
- [ ] Shared error codes ([`api-style.md`](../02-architecture/api-style.md))

## 5. Security

- [ ] [`threat-model.md`](../02-architecture/threat-model.md) updated
- [ ] S4B creds in vault only
- [ ] Escape test catalog referenced

## 6. Score (Doc)

- [ ] Update [`PROGRESS.md`](PROGRESS.md) **Doc** column
- [ ] List Gaps where Doc < Target
- [ ] Block merge if any mandatory Doc item < 6

## 7. Implementation readiness (Impl)

- [ ] Update **Impl** column in [`PROGRESS.md`](PROGRESS.md)
- [ ] Check blockers in [`implementation-readiness.md`](implementation-readiness.md)
- [ ] Per-module matrix in [`10-implementation/module-readiness.md`](../10-implementation/module-readiness.md)
- [ ] Before starting code iteration I*: Impl avg ≥ 7, no module Impl < 5
- [ ] Contracts in repo: OpenAPI paths, JSON Schema, seeds — not «later in code»

## Module review (×10)

For each `docs/03-modules/M*/checklist-review.md`:

1. Run checklist items
2. Score 1–10 per section
3. File issues in Gaps column of PROGRESS.md
