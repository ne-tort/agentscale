# MIG-EQ-02 promotion record

- Record ID: `MIG-EQ-02:blocked:preflight:2026-11-01`
- Task: `MIG-EQ-02`
- Status: `blocked`
- Partition: `none admitted; canonical tenant/owner/source/supplier inventory absent`
- Source snapshot/schema/digest: `not acquired / not inventoried / unavailable`
- Run/checkpoint: `typed contract added; no backfill or projection run executed`
- JSONB association rows/edges: `0 observed; no source acquisition performed`
- Typed requests/lines/groups/tenders: `fixtures only; no positive writes`
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

- Typed immutable records for requests, request lines, candidate groups, tenders, domain evidence, projections, human selections, and procurement export preparation.
- Fail-closed tenant/owner/source/supplier scoped checks across all typed associations. Supplier/source identity remains separate even for equal part numbers.
- JSONB ordered-association backfill contract preserving array order and duplicate occurrences with ordinals and retaining original row evidence.
- Deterministic projection contract with canonical digest, duplicate/reference/scope checks, and empty positive payload on blocked projection.
- Human-only selection and procurement export contracts with actor, reason, line/tender scope, and explicit selection requirements.
- Acceptance fixtures and declarations under the exclusive migration test zone; intentionally not executed.

## Upstream blockers and gate

1. Canonical owner/source evidence and tenant/supplier mappings are not supplied. The contract fails closed and makes no identity inference.
2. No bounded JSONB partition inventory, persistence/CAS/idempotency adapter, source schema, generation-fenced publisher, or audit store was supplied; this change defines ports/contracts only.
3. No accepted upstream handoff or coordinator promotion decision is present.
4. Acceptance fixtures were intentionally not executed under migration verification freeze.

Promotion remains blocked until owner/source evidence, JSONB inventory and order-preservation proof, persistence/idempotency/CAS, deterministic replay, human authorization/audit, and coordinator approval are supplied and acceptance checks are explicitly authorized and pass.

## Rollback

No source data, canonical state, projection, selection, or export was written; rollback is a no-op. Keep the current/legacy owner unchanged. If a later partition fails, stop that partition, preserve JSONB/source evidence and accepted projection, keep the last accepted read model, and repair/rebuild forward. Never rewrite or delete original association evidence as rollback.

This is an immutable blocked preflight, not a promotion, source inventory, production run, schema readiness claim, or cutover authorization. Corrections require a superseding record linked to this ID.
