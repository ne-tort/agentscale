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

## Deliberately not done (outside this task's file zone)

* `evidence_ports.py` still lists `m04_scope_evidence_writer_absent` in `SCHEMA_BLOCKERS`.
  That text is now stale, but the module belongs to another task.
* `composition.py` does not construct the writer, so nothing calls it in production yet. The
  writer is import-safe and fail-closed on its own; wiring is a composition task.
* No RLS policy for the table is installed yet (revision `2026110313` only creates it); the
  writer binds the tenant GUCs and leaves the policy gate to the RLS follow-up task.

## Not executed

Per the migration-stage verification freeze, no test, build, lint, formatter, Alembic or
database command was run for these artefacts. The behaviour above was reasoned from the
writer, the reader (`review_m04_scope_evidence`), the model constraints and the M04-02
contracts.
