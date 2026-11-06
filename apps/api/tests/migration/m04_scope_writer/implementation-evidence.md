# MIG-M04-WRITER-01 — trusted writer for `m04_scope_promotion_evidence`

## Problem this task closes

Revision `2026110313` created `m04_scope_promotion_evidence` and
`SqlPartitionHandoffReadStore.read_partition` now consumes a `ready` row from it
(`MIG-EVIDENCE-M04READ-01`), but no production code appended such a row:
`M04ScopePromotionEvidenceRow(` occurred only in the model declaration. Every live partition
therefore refused with `m04_scope_promotion_evidence_missing` and the M04 -> M06 chain could
not close.

This task adds the producer side of that reader contract.

## Production change

`apps/api/src/prodavan/application/migration/m04_scope_evidence_writer.py`

* `scope_evidence_row_values(write, *, scope)` — pure builder of the exact column mapping;
  every refusal that must precede SQL lives here and raises a named
  `MigrationEvidenceBlocked` (or `MigrationEvidenceConflict`).
* `SqlM04ScopeEvidenceWriter.append(write)` — one transaction per append, tenant GUCs
  installed and read back by `bind_evidence_transaction` before any statement, insert,
  in-transaction readback compared field by field.
* `plan_scope_evidence_append(stored, values)` — insert / replay / typed conflict, decided
  from the row already read (mirrors `plan_marker_append` / `plan_promotion_append`).
* digests: `m04_scope_canonical_digest`, `scope_checkpoint_digest`, `rls_policy_digest`,
  plus the envelope digest for `gate_evidence_digest`.

### Why the canonical digest is the M04-02 digest

`review_m04_scope_evidence` compares the stored `canonical_digest` with the M05 promotion
record's `M04_scope` gate digest, which is `M04ScopeGateEvidence.canonical_digest`. The
M04-02 coordinator only accepts a resolution whose `canonical_digest` equals
`ScopeMigrationCoordinator._canonical_digest(entry, canonical_schema_version)`. The writer
therefore digests the same material with the same canonical encoding:

```
{canonical_schema_version, identity: [kind, scope_id, tenant_id,
                                     cabinet_id, project_id, plugin_instance_id],
 owner_state}
```

A refusal row digests `owner_state = null`, which no accepted M04-02 row can produce, so a
`blocked`/`quarantined` row can never be mistaken for acceptance.

### Append-only identity

`revision_id` is derived from the immutable evidence content (`sha256:` + 64 hex), so:

* the same resolution names the same revision -> replay returns the recorded row, nothing
  is written;
* different evidence names a *new* revision -> a forward append; the older row is untouched;
* a caller may pin a fixed `revision_id`; changed content under it is a typed
  `m04_scope_evidence_revision_content_conflict` listing the differing fields.

`id` is `m04scope:` + 64 hex, derived from the partition and revision, inside the 80-char
column.

### Redaction

Only identifiers, digests, counts, gate names, policy names and redacted reason codes reach
a column. `gate_evidence` is an envelope of *derived* evidence (scope, registry state,
resolution verdict, parent cardinalities/lifecycle, actor membership tuple, RLS enforcement
booleans, checkpoint identity). `_assert_redacted` walks the envelope at every depth and
refuses a payload-shaped key instead of dropping it.

### Unproven RLS identity on a refusal row

`rls_security_review_id` and `rls_database_role` are `NOT NULL` and non-empty for every row,
but a `blocked`/`quarantined` row may have no reviewed RLS proof. Inventing a review id would
be a false statement, so the columns carry the explicit markers
`unproven:rls_evidence_missing` / `unproven:database_role_missing`. A `ready` row can never
carry them: `_ready_evidence` refuses an unproven RLS proof first.

## Acceptance artefacts in this package

| File | What it pins |
|---|---|
| `acceptance_cases.py` | matrix: case -> clause -> artefact, with the two non-executable cases marked |
| `acceptance_fixtures.py` | fixture-free builders plus a statement-level `FakeSession` (no database) |
| `test_writer_acceptance.py` | writer -> reader round trip, digest parity with the coordinator, replay, conflict, forward revision, insert-race and constraint classification |
| `test_writer_refusals.py` | every refusal, each proven to happen before any SQL |
| `test_writer_contract.py` | append-only AST scan, immutable-field parity with the model guard, outcome vocabulary, declared columns, redaction depth, transaction shape |

Covered, per the task's acceptance list:

1. a ready row is created and **accepted** by `review_m04_scope_evidence`, for all three
   typed owner scopes;
2. the repeat is idempotent (replay, no second row);
3. a changed digest is a typed conflict (fixed revision) / a forward revision (derived);
4. `plugin_instance` is refused with `plugin_instance_scope_denied` and no row;
5. `ready` without an actor is refused (and the database check is asserted to exist);
6. non-ready without a `reason_code` is refused (and the database check is asserted);
7. a foreign partition is refused before any SQL (`ExplodingSessionFactory`);
8. append-only: no `UPDATE`/`DELETE` statement, ORM call or import anywhere in the module.

### Check-priority note (MIG-M04WRITER-TESTS-01)

Point 4 is asserted on both sides of the contract: the writer (`scope_evidence_row_values`)
and `ScopeParentEvidence.invalid_reason_for`. The writer already returned
`plugin_instance_scope_denied`; the contract did not — it ran the tenant/parent legs first and
answered `cross_tenant_parent` whenever the tenant leg did not line up. That was the defect, not
the expectation:

* `plugin_instance` is not a kind M04-02 can represent at all (it is absent from
  `ck_m04_scope_promotion_scope_kind` and from `m04_scope_anchor`), so the denial is
  **terminal** — no parent evidence, however complete, changes it. `cross_tenant_parent` is a
  mismatch of the tenant leg, which a caller can legitimately retry with corrected evidence;
  using it for an unrepresentable kind invites exactly the wrong recovery.
* Every other M04-02 entry point already denies the kind first:
  `ScopeResolver.resolve` and `evaluate_active_scope_evidence` both return
  `plugin_instance_denied_m05` before their `context.usable` / tenant / `matches_scope` checks,
  and `scope_evidence_row_values` refuses before reading the anchors' parents. Making
  `invalid_reason_for` the one place that consults parents first was an inconsistency, not a
  deliberate "do not reveal the kind before the tenant is proven" rule — the kind is already
  revealed by the caller's own anchors, so there is nothing to conceal.
* The same reason code now also covers any other kind the contract does not model (the
  fall-through stayed a terminal refusal rather than becoming a parent reason).

## Deliberately not done (outside this task's file zone)

* `evidence_ports.py` still lists `m04_scope_evidence_writer_absent` in `SCHEMA_BLOCKERS`.
  That text is now stale, but the module belongs to another task.
* `composition.py` does not construct the writer, so nothing calls it in production yet. The
  writer is import-safe and fail-closed on its own; wiring is a composition task.
* No RLS policy for the table is installed yet (revision `2026110313` only creates it); the
  writer binds the tenant GUCs and leaves the policy gate to the RLS follow-up task.

## Executed

The migration-stage verification freeze was lifted; these artefacts are run, not only
reasoned about. From `apps/api` with `PYTHONPATH=<worktree>/apps/api/src`:

| command | result |
|---|---|
| `python -m pytest tests/migration/m04_scope_writer -q` | 81 passed |
| `python -m pytest tests/migration/m04_scope tests/migration/m04_scope_evidence tests/migration/evidence_ports -q` | 293 passed |
| `python -m pytest tests/migration/m04_scope_writer tests/migration/m04_scope tests/migration/m04_scope_evidence tests/migration/evidence_ports -q` | 374 passed |
| `python -m pytest tests/migration/m06_grants_gateway tests/migration/migration_composition -q` | 161 passed |

The last two suites were run because they consume `evaluate_active_scope_evidence` /
`invalid_reason_for`: the reordering must not change any accepted path. No database command,
build, lint or Alembic step was run.
