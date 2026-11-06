# MIG-M05-02 acceptance fixtures

Status: `blocked` (fail-closed), baseline `2026-11-01`.

The executable acceptance suite is authored but intentionally not executed. Fixtures do not prove schema installation, source inventory, RLS, persistence atomicity, production resolution, or cutover readiness.

## Upstream and runtime gates

M05 consumes M04 typed-scope resolution and M04E definition/version export through ports. Required evidence is partition-bound `(tenant_id, app_definition_id)` and digest-linked: active parent/scope proof, M04 typed constraints and RLS validation, M04E gates `MIG-M04-01`, `MIG-M04-02`, `MIG-M04E-01`, active-scope/same-definition/immutable-artifact proofs, completed version checkpoint, and persisted `MIG-M04E-02` handoff marker. M05 additionally requires exact instance key, both reciprocal registry-instance FK validations, artifact validation, RLS review, runtime and writer CAS review, stale-worker denial evidence, audit atomicity, and exact source snapshot identity. M04, M04E and M05 source snapshot digests are not assumed equal; M05 carries explicit M04 canonical digest and M04E export digest, while its own candidate reader/checkpoint is pinned to the M05 instance snapshot. M05 also needs a separate active `plugin_instance` registry lookup proving `scope_id == plugin_instance_id == exact instance`, tenant binding, installation cabinet/project equality and both reciprocal registry-instance FK validations. A boolean readiness flag or caller-provided identity is not evidence.

Incomplete evidence blocks before reading candidates, changing cursor/generation/lifecycle, resolving a positive instance, or publishing.

## Required scenarios

1. Missing M04, M04E, instance key, either reciprocal FK, RLS, fencing, audit evidence, or subject/session proof blocks before candidate reads or persistence.
2. Resume is bound to partition, source snapshot ID, schema version and source digest. Ahead, mismatched, absent noninitial, or stale caller cursor cannot choose a different page.
3. Reader receives exact immutable snapshot coordinates and bounded page size. Rows are ordered strictly after the durable cursor; duplicate IDs, wrong page size, or nonmonotonic IDs reject the page.
4. Same source key/schema/digest replays immutable original binding and mapping checkpoint while atomically advancing the separate page cursor and recording redacted replay audit.
5. Same source key/schema with changed digest appends conflict/quarantine evidence without overwriting original mapping or identity.
6. Missing exact instance, definition, version, tenant, typed scope, installation anchors, artifact, subject/session, lifecycle, or generations is quarantined and cannot become resolution evidence.
7. M04 resolution must prove exactly one active typed row and parent compatibility. Archived, quarantined, missing, ambiguous, wrong-tenant/cabinet/project rows deny.
8. M04E must resolve definition by trusted tenant/app and version within that definition. Exact version ID, manifest identity, artifact digest and lifecycle must agree. No raw ID/name fallback.
9. Subject, session and tenant are compared to authenticated server context; source/request values never establish authority.
10. One trusted persistence transaction sets RLS context and rechecks parent/definition/version/artifact proof, digest mapping, checkpoint CAS, evidence, quarantine and audit. Any audit/CAS/storage failure rolls all effects back and yields no positive evidence.
11. Resolver identity contains instance, definition, version, tenant, typed scope, installation scope, artifact, subject/session, runtime generation and writer generation. Missing, ambiguous or mismatched identity denies without fallback.
12. Resolver permits only published lifecycle, active runtime, exact generations and matching authenticated context. Draft, disabled, revoked, archived, quarantined, stopped and stale states deny with redacted reason.
13. Runtime restart CAS compares both expected runtime and writer generations and advances both exactly one. Stale epoch, wrong instance, skipped/no-op/backward generation, or nonactive result denies.
14. Writer CAS compares the complete identity plus current runtime/writer epoch. Concurrent stale worker is a zero-row conflict and cannot mutate instance/runtime/lifecycle.
15. Lifecycle transition CAS requires trusted context and both generations; revoked/quarantined are terminal. Publication additionally re-proves active M04 installation scope and M04E definition/version/artifact.
16. Publish CAS result must match exact identity, requested lifecycle, expected runtime generation and incremented writer generation.
17. M06 handoff requires exact accounting of the expected bounded source set, zero conflict/quarantine/unknown/unresolved binding, final-delta checkpoint digest, parity digest, legacy writer fence and all upstream/runtime gates.
18. Handoff record is immutable and records source/canonical/binding evidence, checkpoint, counts, actor, decision owner, timestamp, correlation and writer generation. Missing evidence produces `blocked`.
19. A ready M05 handoff is evidence for M06 review only: legacy remains read/write owner and `canonical_positive_writes=false`. M05 does not authorize grants or cutover.
20. Missing, changed, duplicate, wrong-partition or wrong-checkpoint M04E marker/proof evidence blocks before the instance candidate reader; accepted evidence rows must share the marker correlation and writer/checkpoint lineage.
21. Duplicate exact instance IDs across source rows cannot create two positive mappings; import and M06 handoff both classify the duplicate as unresolved evidence.

22. Every accepted import, resolver result, lifecycle publish, runtime restart, writer-generation advance, and quarantine retry must carry trusted active subject/session proof tied to exact tenant and correlation; missing adapter blocks or quarantines.
23. Same-digest import replay re-proves the exact identity and active subject/session binding; it must not refresh a stale binding or bypass duplicate-instance detection. Changed identity under the same source digest becomes a non-positive blocked conflict.
24. Quarantine retry appends contiguous immutable attempts only for the original `(partition, source snapshot, source/schema key, digest, mapping checkpoint)`; mismatched digest/checkpoint/correlation, non-quarantine source, duplicate attempt, stale attempt-count CAS or audit failure cannot replace original quarantine.
25. M06 handoff includes every retry attempt digest and accepts a resolved quarantine only when its complete contiguous attempt history matches the original quarantine and latest retry proof; retries never alter source accounting or positive-write ownership.

## Safety, rollback and freeze

- Ports only: no ORM, Alembic, DDL, live source data, backfill execution, production resolver wiring, environment mutation or cutover.
- Persistence adapter must perform transaction-local tenant/RLS/parent proofs and database-side CAS; app-side read/compare/write is insufficient.
- The inspected M04-01/M04-02 promotion evidence is blocked, M04E release readiness is `False`, and the M04E promotion record has no admitted partition/rows or handoff marker. Positive-path fixtures in `test_runtime_acceptance.py` represent conditional contract evidence only; they are not operational readiness evidence.
- Audit fields are limited to event/outcome/reason/correlation and numeric generations; never raw payload, credentials, token, subject/session, tenant/scope labels or full instance identity.
- Legacy remains read/write owner. On later partition failure, halt only that partition; preserve mapping, cursor, quarantine and audit; repair forward. Do not delete security evidence to simulate rollback.
- Tests, build, lint, format, compile and git checks were intentionally not run.
