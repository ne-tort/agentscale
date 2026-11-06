# MIG-M06-03 promotion record

- Record ID: `MIG-M06-03:tenant-a:definition-a:blocked:2026-11-01`
- Task: `MIG-M06-03`
- Status: `blocked` (promotion remains disabled; local state-machine implementation is contract-only)
- Scope: immutable lineage for one `(tenant_id, app_definition_id)` partition per command; bounded command payload and fenced revision/CAS transition
- Current evidence: fixture-only `(tenant-a, definition-a)`; no production owner marker, upstream gate bundle, or positive cutover proof supplied
- Required upstream digest chain: M04 scope -> M04E definition/version -> M05 runtime -> M06 parity, each bound to the same partition and source snapshot with parent evidence ID/digest links
- Final delta: requires an immutable writer-drain receipt, zero active legacy writers/claims, matching fencing epoch and final source sequence; repository CAS must compare the live source tail against that frozen sequence
- Current decision/read/write owner: `legacy` / `legacy` / `legacy`; no partition was promoted
- Canonical positive writes: `false`; a positive transition additionally requires verified M06 canonical snapshot/report digests and audit/parity counters
- Rollback policy: append-only pre-read-cutover rollback requires fresh legacy-restoration evidence; post-read-cutover legacy rollback is forbidden and canonical forward repair keeps reads canonical while writes are fenced
- Promotion and rollback records: immutable partition-bound records include command/record digest, actor/time, exact read/decision/write owners, epoch/fencing token, prior-record link, all gate references and final-delta sequence
- Promotion/cutover authorized: `false`; actual upstream proof and trusted verifier/repository wiring are not supplied
- Verification freeze: acceptance tests were authored but intentionally not run; tests, builds, lint, formatting, compilation and Git checks remain unexecuted

## Gate decision

The command handler is fail-closed. Positive transition remains blocked until trusted, same-partition M04, M04E, M05 and M06 evidence is verified with full parent-linked digests, matching source/canonical snapshots, final-delta checkpoint, a drained legacy writer fence and an atomic live-source-tail comparison. The proof must include a positive writer epoch, non-empty bounded observations, zero unexplained mismatches, zero unresolved positive bindings, zero unknown mappings, zero stale-generation acceptances and complete audit. Missing evidence does not count as zero.

A successful promotion changes the partition's canonical read, decision and write owners together, marks the legacy projection read-only and records exact gate digests plus final-delta sequence. It does not claim production integration: the repository must implement one atomic state/evidence transition, revision and lease fencing, and compare-and-set of the live source tail. Uncertain failures reconcile only through command ID and immutable lineage; no record is deleted or rewritten.

## Acceptance and rollback

Acceptance fixtures are in `apps/api/tests/migration/m06_cutover/acceptance_fixtures.py`; contract assertions are in `apps/api/tests/migration/m06_cutover/test_acceptance_contract.py`. They cover fail-closed missing parity, all parity counters, partition isolation, lease expiry/partition/epoch fencing, read-owner versus decision-owner separation, legacy projection marker, immutable evidence, CAS races, idempotency, and rollback. Per migration verification freeze, tests/build/lint/format/compile/git checks were not run.

Rollback is permitted only before canonical read cutover. It requires a fresh, unexpired lease and immutable proof that legacy ownership is restored, canonical claims are stopped and drained, and no legacy claims remain unresolved. It restores the exclusive legacy read/decision/write owner and appends a separate rollback record. After canonical read cutover there is no legacy rollback authority; forward repair retains canonical reads, fences writes on uncertainty, and restores canonical writes only after fresh verified reconciliation.

## Risks and dependencies

- M06-02 parity/handoff is explicitly not ready; no cutover is safe or authorized now.
- This task defines repository protocols but does not provide a durable database implementation, transaction isolation, lease issuer, or production wiring; those are required before operational use.
- Append-evidence-before-CAS can leave a blocked/intent record after CAS conflict; reconciliation must preserve it and use a new command ID for a subsequent attempt.
- Read-only legacy projection enforcement must be wired to actual read/write adapters and verified before promotion; a state marker alone cannot enforce it.
- The target module and tests are frozen to the requested exclusive zones. No formal verification or git checks were run, and no commit/push was made.
