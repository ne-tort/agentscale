# MIG-EVIDENCE-01 — implementation evidence

Task: **MIG-EVIDENCE-01**, first cutover break-point of
[`docs/08-migration/integration-seams-review.md`](../../../../../../docs/08-migration/integration-seams-review.md) §2
(§3 G1–G4). Status: **implemented; not executed** (migration-stage verification freeze).

Production module: [`apps/api/src/prodavan/application/migration/evidence_ports.py`](../../../../src/prodavan/application/migration/evidence_ports.py).

## 1. Ports closed

| Port (declaration) | Implementation | Target table(s) / source |
|---|---|---|
| `M05HandoffPort.append_marker` — `m04e_definition_version/backfill.py:1112` | `SqlM05HandoffEvidenceStore` | `m04e_definition_version_promotion_evidence` (revision `2026110302`) |
| `EvidenceStore` — `m04e_definition_version/backfill.py:280` (`find_existing`, `find_observation`, `record_blocked`, `classify_and_commit_page`) | `SqlM04EBackfillEvidenceStore` | `m04e_definition_version_promotion_evidence` (`2026110302`) |
| `M05PromotionRecordPort.append_immutable` — `m05_instance_runtime/runtime_service.py:1425` | `SqlM05PromotionEvidenceStore` | `m05_plugin_instance_promotion_evidence` (`2026110303`) |
| `PartitionHandoffReadPort.load_partition` — `m06_grants_gateway/handoff.py:120` | `SqlPartitionHandoffReadStore` | `m05_plugin_instance_promotion_evidence`, `m05_plugin_instances`, `m04e_definition_version_promotion_evidence`, `m04_scope_promotion_evidence` (`2026110313`), `m06_canonical_grant_promotion_evidence` (`2026110304`), `dynamic_app_capability_grant_mappings` + `dynamic_app_capability_grants`, live `pg_class`/`pg_policies` |

All rows read and written are rows of registered `Base.metadata` tables or live catalog
state. There is no dict-backed store, no test double and no `IS_TEST_DOUBLE` marker in the
module (pinned by `test_contract_surface.py`).

## 2. Contract decisions and why

1. **Append-only with an immutable key *and* a logical key.** `append_marker` reads the
   primary key (`record_id`) and the logical evidence key
   (`tenant‖source_snapshot_digest‖canonical_digest‖checkpoint_digest`) under the bound
   tenant, then decides insert / replay / conflict in the pure
   `plan_marker_append`/`plan_promotion_append`. An exact repeat returns the recorded
   object; a divergent marker for either key raises `MigrationEvidenceConflict` (a
   `MigrationEvidenceBlocked` subclass) and never overwrites. `append_immutable` returns
   `None` — the port signature requires it — but still performs the same replay/conflict
   decision and inserts nothing on replay.
2. **`outcome` cannot express `ready`.** `ck_m04e_promotion_outcome` and
   `ck_m05_instance_promotion_outcome` admit only `('blocked','quarantined')`. Both handoff
   contracts carry `ready`. The module stores the exact status inside the redacted
   `gate_evidence` JSON envelope and writes the column as `blocked`/`quarantined`
   (which is also the tables' documented meaning: "never enables a positive writer").
   `decode_marker_row` / `decode_promotion_row` reconstruct the contract object exactly, so
   an exact retry compares equal. This is a **schema limitation, not a workaround for a
   missing column**: `SCHEMA_BLOCKERS[0]`.
3. **M04E has no checkpoint or source-mapping table.** G4 assumed one. The page cursor and
   the first-mapping/observation ledgers live in the envelope of one append-only row per
   committed page; `revision_id` (the digest of the *expected* checkpoint) is the
   compare-and-set token through `uq_m04e_promotion_revision`, so two pages claiming the
   same cursor collide at the database, and the same page replayed hits the primary key.
   `SCHEMA_BLOCKERS[4]`.
4. **Trusted context, never guessed.** Every statement runs after
   `bind_evidence_transaction` sets `app.tenant_id`/`app.writer_generation`/
   `app.writer_epoch`/`app.migration_role`/`app.audit_writer` and reads them back. A datum
   whose partition is outside `TrustedEvidenceScope.tenant_id`, or whose
   `writer_generation` differs from the bound one, is refused **before any SQL** (pinned
   with `ExplodingSessionFactory`).
5. **Absence is `None` or a named refusal.** `load_partition` returns `None` for an
   unfillable partition and exposes `read_partition` → `PartitionHandoffRead`, which is
   structurally either accepted evidence or a non-empty blocker tuple, never an empty
   object.
6. **Redaction is enforced, not filtered.** `_assert_redacted` raises
   `redaction_violation:*` on a `payload`/`manifest`/`token`/`secret`/`password`/`body`
   key instead of dropping evidence silently. Stored payloads carry identifiers, digests,
   counts, gate names and reason codes only; artifact bytes, manifests and request bodies
   never reach a column.
7. **`m04_scope` and `writer_epoch` are refused, not invented.**
   - ~~No registered table stores the accepted M04-02 scope/RLS promotion row~~ — now
     stored in `m04_scope_promotion_evidence` and read conditionally by
     `read_partition`; see §6 for the read, its refusals and the remaining writer gap.
   - M05 permits `writer_generation = 0`, `AcceptedPartitionEvidence` requires
     `writer_epoch > 0` (review finding I3). The module refuses with
     `writer_epoch_not_positive`; it does not normalise `generation + 1`.
   - `m04_rls_digest` is recomputed from the live catalog and compared against a
     `ReviewedM04RlsEvidence` manifest; no approved manifest exists in the repository, so
     the default is `None` and the read refuses with `m04_rls_review_manifest_missing`.
     The policy digest is byte-identical to
     `infra/.../migration_scope/adapter.py::_policy_digest` (pinned by a test), so the two
     reviewers cannot disagree about the same rows.
8. **Accepted inventory is the M06 grant-source inventory, not the instance list.**
   `AcceptedPartitionEvidence.admits(LegacyGrantSource)` compares grant sources, so
   `_load_inventory` reads `dynamic_app_capability_grant_mappings` joined to its target
   grant (tenant/definition/version/instance anchors + `revoked_at IS NULL`) instead of
   `m05_plugin_instances` sources. `SCHEMA_BLOCKERS[2]` records that the M05 promotion
   record itself carries counts only.

## 3. Acceptance artefacts

Executable (pure, no database — see §4 for what was deliberately not run):

| File | Covers |
|---|---|
| `test_contract_surface.py` | the four ports are implemented with matching signatures and return annotations; no in-memory/test-double path; no `UPDATE`/`DELETE`/`merge` statement; no hand-managed sub-transaction; every target table is registered; blockers recorded; no defaulted dependency except the reviewed-RLS manifest |
| `test_identity_and_replay.py` | ledger keys are stable and hide the partition; exact marker/promotion repeat is a replay (by id *and* by logical key); a changed digest is a conflict, never an overwrite; conflict on key collision; payload round-trips through the decoders |
| `test_payloads.py` | mapper keys ⊆ registered model columns; `outcome` stays inside its check constraint while readiness survives in the envelope; column widths; redaction refuses carrier keys; blocked markers fall back to an envelope digest instead of a placeholder; positive-write and partition-drift refusal |
| `test_refusals.py` | foreign tenant refused before SQL (all four ports); absent row → `None`; every request key of `read_partition` is used; missing trusted generation; checkpoint outside lineage; half-filled read result rejected; M04 RLS refusals named |
| `test_backfill_cursor.py` | durable-cursor CAS (cursor, sequence, phase, lineage, writer generation); successor arithmetic (`next_checkpoint`) for advance / phase switch / completion / all-replay / first-time conflict / empty page; checkpoint digest stability |
| `test_partition_assembly.py` | a complete partition yields exactly one `AcceptedPartitionEvidence`; canonical ordering; 16 parametrised named refusals; `writer_epoch = 0` refused; non-sha256 gate digest refused; partition-key widths; reviewed-manifest shape; policy-digest parity with the reviewed M04 adapter |

`acceptance_cases.py` is the machine-readable matrix; it marks two cases
`executable=False` with the reason (live-database insert/RLS) so the matrix cannot be read
as a green suite.

## 4. Freeze

Per the migration-stage protocol this task calls out that **no tests, builds, compilation,
lint, formatter, `alembic`, DDL or git command were run**. A read-only import of the new
module plus `ast.parse` and `pyflakes` (static, no execution of the acceptance cases) was
used to confirm the files are syntactically valid and free of undefined/unused names. The
behavioural claims above are derived from the contracts and the called code, not from a
test run.

## 5. Remaining blockers and risks

1. ~~`m04_scope_promotion_evidence_missing`~~ — **closed by MIG-DDL-M04EVID-01 +
   MIG-EVIDENCE-M04READ-01** (§6): `m04_scope_promotion_evidence` stores the row and
   `read_partition` now reads it. What remains is that no production writer appends those
   rows yet, so a live partition refuses with the same text until trusted M04-02 tooling
   records a `ready` row (`SCHEMA_BLOCKERS: m04_scope_evidence_writer_absent`).
2. `m04_rls_review_manifest_missing` — an approved M04 policy manifest must come from
   trusted deployment wiring. Until then the read refuses.
3. `promotion_outcome_cannot_express_ready` — a reviewer querying `outcome` sees `blocked`
   for a ready handoff; the true status is only in `gate_evidence`. A follow-up DDL task
   should widen `ck_m04e_promotion_outcome` / `ck_m05_instance_promotion_outcome` and then
   the mapper can write the status directly.
4. `m05_writer_generation_zero_has_no_m06_epoch` — the domain gap (review finding I3) is
   still open; the ports refuse rather than translate.
5. `m06_schema_identity_digest_missing` — the M06 promotion row must carry
   `dependency_digests["schema_identity_digest"]`; the read refuses instead of recomputing
   the live value as if it were stored evidence.
6. `m04e_backfill_has_no_checkpoint_or_source_mapping_table` — the cursor and ledger live in
   the envelope. It is append-only and CAS-safe, but a reviewer cannot query the cursor with
   plain SQL columns; a dedicated table would be better and is a DDL task.
7. **Not wired.** No composition root constructs these ports (`core/wiring.py` still has no
   migration mention, and `core/**` is outside this task's zone). The ports are callable and
   self-sufficient, but the chain is still not assembled into a process — that is the
   remaining §7 work of the seams review, not this task.
8. Concurrency: two writers racing the same page/marker rely on the unique constraints
   (`IntegrityError` → `MigrationEvidenceConflict`). That is correct for evidence, but the
   caller must treat it as retry-after-read, not as success.

## 6. MIG-EVIDENCE-M04READ-01 — the M04-02 scope link is read, not declared

Revision `2026110313` created `m04_scope_promotion_evidence` (model
`M04ScopePromotionEvidenceRow`, RLS policy from `2026110314`), so the unconditional
`blockers.extend(DECLARED_READ_BLOCKERS)` in `read_partition` was refusing on an artefact
that exists. That line is removed; the link is now a real, conditional read.

How the row is selected and verified:

1. **Owner scope.** The M05 instance row is always `scope_kind = 'plugin_instance'`
   (`ck_m05_instance_typed_scope_shape`), which the M04-02 contract refuses as a scope
   owner, so the partition's M04-02 scope is the owner anchor the instance is installed at:
   project if set, else cabinet, else the tenant (`m04_scope_anchor`). A shape that is not
   one of the three typed scopes yields no anchor and a named refusal.
2. **Latest revision.** `_load_m04_scope_evidence_row` runs inside the tenant-bound
   transaction and filters `tenant_id` + `scope_kind` + `scope_id`, ordered by
   `recorded_at DESC, revision_id DESC` with `LIMIT 1`, so the newest append-only revision
   of that exact partition is the one that must be `ready`.
3. **Verification** (`review_m04_scope_evidence`): the row must belong to the bound tenant,
   carry the typed scope shape of the anchor, name the same `scope_registry_id` the instance
   is bound to, have `outcome = 'ready'`, have all five sha256 digest columns well formed,
   and have a `canonical_digest` equal to the `M04_scope` gate digest the M05 promotion
   record cites. Only then is it consumed.

Refusals — exact and conditional:

| Condition | Blocker |
|---|---|
| no row under the trusted tenant for the owner scope | `m04_scope_promotion_evidence_missing` |
| `outcome` is `blocked`/`quarantined` (or anything not `ready`) | `m04_scope_evidence_not_ready` |
| digest column malformed, or `canonical_digest` != M05 `M04_scope` gate | `m04_scope_evidence_digest_mismatch` |
| row of another tenant / scope shape / registry row | `m04_scope_evidence_tenant_mismatch` / `m04_scope_evidence_scope_shape_mismatch` / `m04_scope_evidence_registry_mismatch` |

A healthy row adds **no** blocker, so a partition with all five links healthy now returns
`AcceptedPartitionEvidence`. `PartitionParts` gained `m04_scope_evidence_row_id` and
`m04_scope_evidence_digest` (both required), and `assemble_partition_evidence` re-checks
them so a caller cannot assemble a partition that never read the row.

`DECLARED_READ_BLOCKERS` now lists the three conditional refusals (exported so a reviewer
can compare declared wording with what is raised); `SCHEMA_BLOCKERS` drops the stale "no
registered table" entry and records the two facts that remain true: no production writer
appends M04-02 rows yet (`m04_scope_evidence_writer_absent`), and the row is not compared
with the requested writer epoch (`m04_scope_evidence_row_is_not_epoch_bound`) — the M05
`M04_scope` gate digest is what binds it to the partition. `promotion_outcome_cannot_
express_ready`, `m05_promotion_record_carries_no_source_inventory`,
`m05_writer_generation_zero_has_no_m06_epoch` and `m04e_backfill_has_no_checkpoint_or_
source_mapping_table` are left untouched because they are still true.

Acceptance artefact: `test_m04_scope_read.py` (ready/missing/not-ready/digest-mismatch,
foreign tenant and foreign scope, statement shape, conditional blocker, assembly input
checks, tenant-context refusals before SQL). The other four reads (M05 record, M04E marker,
instance, M06 row) are unchanged and pinned by `test_the_other_four_reads_are_unchanged`.

