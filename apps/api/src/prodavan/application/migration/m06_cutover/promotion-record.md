# MIG-M06-03 promotion record

- Record ID: `MIG-M06-03:tenant-a:definition-a:blocked:2026-11-01`
- Task: `MIG-M06-03`
- Status: `blocked` (bounded cutover command/state machine implemented; no partition promoted)
- Scope: one `(tenant_id, app_definition_id)` partition per command; bounded command payload, CAS revision, no global switch
- Partition reviewed: `(tenant-a, definition-a)`; this is a fixture/evidence scope, not authorization to promote
- Upstream M06-02 parity gate: `not_ready`; trusted partition handoff/report digest, bounded observation evidence, final-delta checkpoint and verification are unavailable
- Current parity evidence: no accepted report; no observations/deltas claimed; all unmeasured counts remain unknown, not zero
- Writer lease/epoch/fencing: command contract requires partition-bound unexpired lease, monotonic epoch and fencing token; runtime lease issuer/production repository integration is not supplied by this task
- Current decision owner: `legacy`
- Current canonical read owner: `legacy`
- Legacy projection marker: `read_only=false` until a valid promotion
- Canonical positive writes: `false`; decision owner is deliberately unchanged by this read transition
- Promotion/cutover authorized: `false`
- Immutable evidence: append-only promotion/blocked/rollback record includes partition, actor/time, before/after state and read owner, epoch/fencing token, parity digests/checkpoint, stable reason and canonical SHA-256 record digest; state and evidence commit through one atomic repository transition
- Actor/time: implementation agent; record dated `2026-11-01`; coordinator review/signature pending

## Gate decision

The command handler is fail-closed. Current M06-02 status blocks promotion with `m06_02_parity_gate_not_ready`; the command transitions the selected partition to `blocked` using CAS, appends blocked evidence, and leaves canonical read ownership, decision ownership, and writer epoch unchanged. Promotion can only be considered after a trusted, partition-matching M06-02 handoff verifies its report and source snapshot digests, final delta checkpoint, positive writer epoch, non-empty bounded observations, zero unexplained mismatches, zero unresolved positive bindings, zero unknown mappings, zero stale-generation acceptances, and complete audit. A lease bound to the same partition must be unexpired and fenced above the current partition epoch.

A successful promotion moves only that partition's canonical read owner to `canonical`, marks the legacy read projection read-only, and retains `legacy` as decision owner. It does not enable canonical positive decisions/writes. Repository integration must provide one atomic transition(expected revision, resulting state, immutable evidence, lease/fencing); split append-evidence plus CAS is forbidden. Uncertain failures are reconciled by idempotency ID and must never delete or rewrite prior records.

## Acceptance and rollback

Acceptance fixtures are in `apps/api/tests/migration/m06_cutover/acceptance_fixtures.py`; contract assertions are in `apps/api/tests/migration/m06_cutover/test_acceptance_contract.py`. They cover fail-closed missing parity, all parity counters, partition isolation, lease expiry/partition/epoch fencing, read-owner versus decision-owner separation, legacy projection marker, immutable evidence, CAS races, idempotency, and rollback. Per migration verification freeze, tests/build/lint/format/compile/git checks were not run.

Rollback is partition-scoped and requires a fresh, unexpired lease with epoch above current writer epoch. It restores canonical read owner to `legacy`, clears the legacy read-only marker and leaves decision ownership `legacy`; it appends a separate immutable rollback evidence record. Existing promotion and parity evidence remain retained. Re-promotion after rollback requires a strictly newer epoch and a newly accepted parity handoff; no evidence deletion or global rollback is permitted.

## Risks and dependencies

- M06-02 parity/handoff is explicitly not ready; no cutover is safe or authorized now.
- This task defines repository protocols but does not provide a durable database implementation, transaction isolation, lease issuer, or production wiring; those are required before operational use.
- Append-evidence-before-CAS can leave a blocked/intent record after CAS conflict; reconciliation must preserve it and use a new command ID for a subsequent attempt.
- Read-only legacy projection enforcement must be wired to actual read/write adapters and verified before promotion; a state marker alone cannot enforce it.
- The target module and tests are frozen to the requested exclusive zones. No formal verification or git checks were run, and no commit/push was made.
