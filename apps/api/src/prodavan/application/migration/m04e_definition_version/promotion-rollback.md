# MIG-M04E-02 promotion and rollback notes

Status: `blocked`; evidence-only contracts are present, but no trusted persistence or upstream proof adapter is configured.

## Promotion gates

Do not promote a partition until a reviewed durable adapter can atomically persist mappings, quarantine/audit evidence and checkpoint CAS, and independently re-derive application-supplied proofs. Required partition evidence:

- retained tenant ownership, active typed parent scope and passing `MIG-M04-01`, `MIG-M04-02` and `MIG-M04E-01` gate digests;
- deterministic tenant-scoped definition and `(definition, manifest_version)` identities, with duplicate and changed-source conflicts quarantined;
- same-definition current-version proof, exact artifact digest equality and database-backed immutable-artifact proof;
- completed version phase, zero unresolved required rows, source-snapshot lineage, durable correlation/audit and checkpoint CAS evidence;
- append-only M05 handoff marker with `canonical_positive_writes=false`; M05 still requires its independent gates.

No database adapter, source inventory, retained owner/scope proof or artifact-immutability record is available in this task. The resolver returns deny/quarantine unless trusted scope and durable definition/version evidence ports return transaction-bound proof. Backfill reads no candidate rows unless source, gate, proof and durable commit ports are supplied. Ports are contracts only; no fake or in-memory adapter is included.

## Rollback

- Before evidence import, this task has performed no writes; rollback is a no-op. Any later schema/object rollback is outside this application task and requires dependency review.
- If a future partition import fails, halt only that partition, retain mappings, quarantine, audit, snapshot lineage and checkpoint, and leave legacy definitions/versions as read/write owner.
- Repair forward from the last durable CAS checkpoint. Never overwrite first-seen source/canonical evidence, reset a changed snapshot cursor, delete imported evidence, or enable a second decision owner.
- Do not enable instance publication, grants, DDL, shared-environment writes or cutover based on an M05 export or marker alone.

## Verification freeze

No tests, builds, compilation, lint, formatting, git checks/actions, DDL, live writes, environment changes or cutover were intentionally run. Acceptance remains unverified pending the migration coordinator's explicit freeze lift.
