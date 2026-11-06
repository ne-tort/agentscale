# MIG-EQ-02 promotion record

- Record ID: `MIG-EQ-02:blocked:preflight:2026-11-01`
- Task: `MIG-EQ-02`
- Status: `blocked`
- Partition: `none admitted; canonical tenant/owner/source/supplier inventory absent`
- Source snapshot/schema/digest: `not acquired / not inventoried / unavailable`
- Run/checkpoint: `port-driven application flow and acceptance suite authored; no source read/backfill/projection run executed`
- JSONB association rows/edges: `0 observed; no source acquisition performed`
- Typed requests/lines/groups/tenders: `fixtures only; no source data or canonical writes`
- Canonical read/write owner: `legacy/current equipment source remains authoritative`
- Projection owner: `none promoted; deterministic target contract only`
- `canonical_positive_writes`: `false`
- `human_selection_enabled`: `false`
- `procurement_export_enabled`: `false`
- Promotion/cutover authorized: `false`
- Actor: `delegated implementation agent`
- Decision owner: `migration coordinator`
- Reviewer/signature: `pending`

## Implemented deliverables

- Immutable typed requests, lines, candidate groups, supplier/source-distinct tenders, budget projections and detailed procurement export rows.
- Port-driven application flow for scoped reads/projection, human selection, export preparation and ordered association backfill; no DB or live persistence adapter is included.
- Fail-closed tenant/owner/project/source evidence and relation checks. Stale, unknown-freshness, on-order or insufficient offers remain visible but cannot be selected or exported.
- JSONB backfill preserves order, duplicates and original row evidence; verifies targets in exact scope and appends with source revision/digest through an idempotent CAS port.
- Human decisions carry actor, reason, project and projection digest; replacement explicitly supersedes the current line selection. Prepared exports contain selected offer provenance and currency totals only.
- Acceptance tests exercise the port-driven workflow; intentionally not executed.

## Upstream blockers and gate

1. Canonical owner/source evidence and tenant/supplier mappings are not supplied. The contract fails closed and makes no identity inference.
2. No bounded JSONB partition inventory, canonical schema/mappings, concrete persistence/CAS/idempotency adapter or durable audit store was supplied. Host adapters must meet documented atomicity and authenticated-identity guarantees.
3. No accepted upstream handoff or coordinator promotion decision is present.
4. Acceptance tests were intentionally not executed under the migration verification freeze. Formal checks and git operations were prohibited and not run.

Promotion remains blocked until owner/source evidence, JSONB inventory and order-preservation proof, persistence/idempotency/CAS, deterministic replay, human authorization/audit, and coordinator approval are supplied and acceptance checks are explicitly authorized and pass.

## Rollback

No source data, canonical state, projection, selection, or export was written; rollback is a no-op. Keep the current/legacy owner unchanged. If a later partition fails, halt only that partition, retain JSONB/source/decision evidence, continue serving the last accepted read owner, and repair/rebuild forward. Never delete original association evidence or submit procurement as rollback.

This is an immutable blocked preflight, not a promotion, source inventory, production run, schema readiness claim, or cutover authorization. Corrections require a superseding record linked to this ID.
