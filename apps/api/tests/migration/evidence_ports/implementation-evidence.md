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
| `PartitionHandoffReadPort.load_partition` — `m06_grants_gateway/handoff.py:120` | `SqlPartitionHandoffReadStore` | `m05_plugin_instance_promotion_evidence`, `m05_plugin_instances`, `m04e_definition_version_promotion_evidence`, `m06_canonical_grant_promotion_evidence` (`2026110304`), `dynamic_app_capability_grant_mappings` + `dynamic_app_capability_grants`, live `pg_class`/`pg_policies` |

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
   - No registered table stores the accepted M04-02 scope/RLS promotion row, so
     `read_partition` always appends `DECLARED_READ_BLOCKERS` and
     `assemble_partition_evidence` refuses with `m04_scope_evidence_id_missing`.
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

1. `m04_scope_promotion_evidence_missing` — no table stores the accepted M04-02 scope/RLS
   row. `load_partition` therefore always refuses; closing it needs a DDL task outside this
   zone (`scope_registry` has no gate-digest columns either).
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
