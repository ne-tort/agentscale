# MIG-M06-02 promotion record

- Record ID: `MIG-M06-02:unpartitioned:blocked:2026-11-01`
- Task: `MIG-M06-02`
- Status: `blocked` (implementation/evidence contract present; no partition promotable)
- Partition key: `(tenant_id, app_definition_id)`; no tenant partition was supplied or admitted
- Source snapshot/schema: unknown; source schema contract is `GrantSet v1`; source snapshot digest unavailable
- Source/canonical/binding digests: runtime-produced by `adapter.py`; no source inventory was supplied, so aggregate digests unavailable
- Checkpoint/final delta: none; checkpoint is required per bounded partition before parity review
- M04 scope handoff: missing
- M04.e definition/version handoff: implementation exists in the workspace, but accepted per-partition handoff marker/digest is missing
- M05 exact instance/runtime/fencing handoff: missing
- M06 schema/trusted writer epoch: missing; current ORM cannot preserve capability key, authenticated subject or exact instance binding
- Runtime integration: evidence facade is not wired into production `CapabilityGatewayService`; production API/request/decision contracts expose neither resolved tenant/scope/instance nor a shadow hook. Integration must wait for a scoped upstream gateway contract and may not replace the legacy decision owner in this task
- Evidence persistence: application requires a trusted atomic `commit_evidence` operation for mapping + quarantine + shadow + audit; no persistence adapter/schema/RLS implementation is present in this zone. Missing atomic persistence proof keeps promotion blocked
- Unknown/unresolved counts: not measured; absent inventory and upstream gates prevent a defensible count
- Mismatch report: no source observations provided; parity remains not evaluated
- Writer epoch/generation: unavailable
- Decision owner and canonical read owner: `legacy`
- Observation window: not started
- Actor/time: implementation agent, `2026-11-01`; coordinator review/signature pending
- Correlation ID: none (no bounded source run)
- `canonical_read_owner`: `legacy`
- `canonical_positive_writes`: `false`
- Gate/rollback review checkpoint: `review-2` (static read-through only; formal checks remain frozen)
- Promotion/cutover authorized: `false`

## Gate decision

No partition is promotable. Before any promotion review, produce retained M04 scope + M04.e + M05 exact-instance handoffs and the M06 schema/RLS/audit/trusted-writer gate, then run a bounded evidence capture after verification freeze is explicitly lifted. Acceptance requires zero unexplained mismatches, zero unknown mappings, zero unresolved positive bindings, complete audit, a final-delta checkpoint, and no stale-generation acceptance.

## Rollback

Before canonical read cutover, stop adapter/mapping/parity writes for only the affected `(tenant_id, app_definition_id)` partition. Keep legacy reads, decisions and writes authoritative. Preserve mapping, quarantine, audit and shadow observations; repair forward and repeat the bounded parity window. Do not delete target evidence or attempt a global rollback. There is no cutover or rollback command encoded by this record.
