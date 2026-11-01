# MIG-M04-02 immutable promotion record

Record ID: `MIG-M04-02:blocked:preflight:2026-11-01`
Status: `blocked`
Partition: `unbounded / none admitted`
Decision: `do not promote; do not run backfill or enable resolver`

## Source and target evidence

- Source snapshot: `not acquired`
- Source schema/version: `not inventoried`
- Source digest / target digest: `not available`
- Checkpoint: `none`
- Counts by scope kind: tenant `unknown`, cabinet `unknown`, project `unknown`, plugin_instance `deferred/denied`
- Counts by quarantine reason: `unknown; no source rows inspected`
- Exactly-one-active-row evidence: `absent`
- Membership and parent compatibility evidence: `absent`
- RLS review: `absent`
- Writer/fencing generation: `not provisioned`
- Correlation ID: `MIG-M04-02:blocked:preflight:2026-11-01`

## Ownership and decision

- Legacy read owner: `legacy owner state`
- Legacy write owner: `legacy owner state`
- Canonical registry read/write owner: `none; no implementation enabled`
- Actor: `delegated implementation agent`
- Decision owner: `migration coordinator`
- Decision time: `2026-11-01` (date only; exact clock time not recorded)
- Reviewer/signature: `pending coordinator review`

## Gate result and blockers

1. MIG-M04-01 accepted tenant/typed-anchor foundation and deletion boundary are unavailable.
2. No active, source-backed cabinet/company membership/access proof or project compatibility evidence is available for a bounded partition.
3. No transaction-local tenant context or RLS review evidence exists in this task zone.

Required outcome: fail closed. No source identities are admitted and no identity is rewritten. Plugin-instance rows remain deny/deferred, with no dependency on or claim of M05 completion.

## Rollback and next gate

No target data was imported, so there is nothing to delete or roll back. Preserve this record as immutable evidence; do not edit it in place. Legacy remains the authority. Resume only in a new record after M04-01 acceptance and bounded, source-backed active membership plus project-parent proof are supplied. Then process one source row per transaction with stable identity/schema/digest, checkpoint, redacted audit, and changed-digest conflict quarantine. Promotion requires zero unresolved required parent mappings, exactly one active typed row per accepted identity, reviewed RLS, and continued plugin-instance deny/deferred status. Any post-import rollback halts only the affected partition and preserves mappings, quarantine and audit while legacy remains authoritative.

This record is evidence of a blocked preflight, not production promotion, schema readiness, import completion, or authorization eligibility. It is append-only: corrections require a superseding record linked to this ID.
