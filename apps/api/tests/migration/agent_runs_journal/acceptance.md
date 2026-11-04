# MIG-AR-JOURNAL-01 — acceptance matrix

SQL storage for the durable provider-attempt journal. Status: **authored only**.
**Nothing here was executed** (migration verification freeze): no `pytest`, no
build, no compile, no lint, no `alembic`, no database access, no git command.
Behaviour is argued from the contract, the code and the rendered PostgreSQL.

Zone:

- `apps/api/src/prodavan/infrastructure/persistence/agent_control_plane/attempt_journal.py`
  — `SqlAlchemyProviderAttemptJournal` over `agent_run_provider_attempts`.
- `apps/api/tests/migration/agent_runs_journal/**` — this matrix and its artifacts.

Out of scope by task definition (read, never modified): `application/agent_runs/attempts.py`
(the contract), `attempt_registry.py` (the row), `models/**`, Alembic,
`application/agent_runs/**`.

## 1. What the module is, and what it is not

The contract module declares the journal twice: as *policy* (`AttemptState`, the
`_TRANSITIONS` matrix, `next_state`, `redact_metadata`, the frozen
`ProviderAttempt`) and as *storage* (`ProviderAttemptJournalPort`). Until this
task no host implemented the port, so every execution host failed closed with
`agent_run_provider_attempt_journal_unwired`.

`attempt_journal.py` implements the storage half only. It **calls** policy
(`next_state`, `redact_metadata`, `_assert_secret_free`) and never copies it. Two
consequences the artifacts rely on:

| Property | How it is guaranteed |
|---|---|
| The legal transition set cannot drift from the matrix | `_legal_targets(state)` is derived by probing `next_state` over the whole `AttemptOutcome` vocabulary — a transition added to `attempts.py` is legal in the store the moment it is legal there |
| `unknown` cannot be laundered into a success | `unknown`'s only legal targets are the two reconciled states, so `unknown → known_*` is `ILLEGAL_TRANSITION` at the *statement* layer, not only at the application layer |

## 2. Operations

| Operation | Contract | Implementation |
|---|---|---|
| `insert_start` / `start_attempt` | idempotent per `idempotency_key`; same digest → stored row with `created=False`; different digest → `AttemptConflict`; never overwrites | one `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING RETURNING attempt_id`; no row returned → read the winner and compare |
| `read` / `read_attempt` | stored attempt or `None` | one `SELECT` by `attempt_id`, optional `tenant_id` narrow |
| `find_unresolved` | newest `started`/`unknown` attempt of a run | one `SELECT ... WHERE run_id = ? AND state IN ('started','unknown') ORDER BY generation DESC, attempt DESC LIMIT 1` |
| `cas_finish` | one `UPDATE ... WHERE attempt_id = ? AND state = ?`, returns row count | pure statement builder; `0` is a lost race and is **returned**, never raised |
| `finish_attempt` (typed) | legal transition applies; illegal/terminal/ambiguous refused | resolves the target through `next_state`, then classifies `rowcount == 0` from the stored row |
| `find_expired_unresolved` (sweep) | `started` attempts past `deadline_at` | bounded `SELECT ... WHERE state='started' AND deadline_at <= ? ORDER BY deadline_at LIMIT 200`, served by `ix_agent_run_attempts_state_deadline` |
| `ensure_ready` | prove the table exists at wiring time | one `SELECT ... LIMIT 1`; a missing table is the named blocker |

`finish_attempt` is an addition, not a replacement: `cas_finish` keeps the
protocol's "`0` is not an error" semantics for `ProviderAttemptJournal`, while
callers that bypass the application tier get a typed refusal. Those are different
callers with different contracts, and conflating them would either force the
application journal to swallow a typed exception or force a direct caller to
guess why nothing was written.

## 3. Required cases (task acceptance list)

### 3.1 Start is idempotent

- `test_start_returns_a_created_attempt_and_issues_one_statement` — one
  statement (`scalar` on the `RETURNING` id), no follow-up.
- `test_start_writes_a_started_row_only` — the statement carries
  `ON CONFLICT (idempotency_key) DO NOTHING` and `RETURNING`.
- `test_repeated_start_returns_the_stored_row_and_does_not_create` — the replay
  returns the *stored* record (`created=False`, including its
  `provider_request_id`), not the caller's copy.
- `test_a_replay_with_the_same_digest_does_not_raise`.

### 3.2 Repeat with a different digest → conflict

- `test_the_same_key_with_a_different_digest_is_a_conflict` — same key, other
  prompt; `AttemptConflict` / `DIGEST_CONFLICT`.
- `test_conflict_leaves_no_room_for_a_second_write` — exactly one insert and one
  read; no `UPDATE` behind the conflict.
- `test_a_stored_row_that_violates_the_contract_is_never_returned` — a key bound
  to a different run is `INVALID_RECORD` (a forged key is a refusal, not a replay).

### 3.3 Completion on every legal transition

- `test_every_legal_transition_applies` — parametrized over all **8** rows of
  `_TRANSITIONS`: `started → {known_succeeded, known_failed, known_retryable,
  unknown, abandoned, discarded_stale}` and `unknown → {reconciled_succeeded,
  reconciled_failed}`. Each asserts one `UPDATE`, no `INSERT`/`DELETE`, and a
  `WHERE` containing both the row identity and the *observed* state.
- `test_the_written_state_is_the_matrix_target` — the `SET state` agrees with
  `next_state` for every outcome.
- `test_no_legal_transition_targets_an_open_state` — no legal transition leaves
  an attempt in `started` or in its own state.

### 3.4 Illegal transition refused

- `test_a_started_attempt_cannot_jump_straight_to_reconciled` — `started →
  reconciled_succeeded` is `ILLEGAL_TRANSITION`, refused **before** any SQL.
- `test_the_raw_cas_refuses_an_illegal_pair_before_issuing_sql`.
- `test_unknown_cannot_be_abandoned_or_discarded_either`.

### 3.5 Repeat completion → already terminal

- `test_a_second_finish_of_the_same_record_is_already_terminal` — refused before
  SQL, so a replay cannot even reach the row.
- `test_a_terminal_attempt_can_never_move_again` — parametrized over all **7**
  terminal states.
- `test_a_finish_that_lost_the_race_to_the_same_state_is_already_terminal` — a
  real race (`rowcount = 0`) is classified from the stored row.

### 3.6 `unknown` cannot be closed by success

- `test_unknown_cannot_be_closed_as_a_known_outcome` — `succeeded`, `failed`,
  `retryable` all give `UNKNOWN_REQUIRES_RECONCILIATION`, with **zero** statements
  issued.
- `test_reconciliation_is_the_only_way_out_of_unknown` — the reconciled write
  still CASes from `unknown`, so reconciliation cannot skip the state.
- `test_a_finish_that_lost_the_race_to_unknown_requires_reconciliation` — a
  worker that raced a reconciler is told to reconcile, not to retry.
- Structural: `test_the_scan_states_are_the_contract_sets`,
  `test_unknown_is_closed_but_not_terminal`.

### 3.7 `find_unresolved` finds expired attempts

- `test_the_sweep_finds_only_started_attempts_past_their_deadline` — rendered
  predicate: `state = 'started'`, `deadline_at IS NOT NULL`,
  `deadline_at <= <now>`, `ORDER BY deadline_at ASC`, bounded `LIMIT`.
- `test_the_sweep_does_not_pick_up_an_attempt_without_a_deadline` — an attempt
  with no deadline is unresolved but never "expired", and `unknown` is out of the
  sweep (it is already decided-for-now).
- `test_the_sweep_is_bounded`, `test_the_sweep_requires_an_aware_clock`.
- `test_the_sweep_returns_the_expired_records` /
  `test_the_sweep_returns_nothing_when_everything_is_still_live`.
- `test_a_started_attempt_blocks_the_run` / `test_an_unknown_attempt_blocks_the_run`
  / `test_a_decided_run_reports_nothing_unresolved` — the per-run question.
- `test_the_scan_is_ordered_newest_first_not_oldest` — `generation DESC,
  attempt DESC`: a closed generation 1 cannot hide generation 2.
- `test_neither_scan_can_change_a_state` — both statements are `SELECT` only: no
  `UPDATE`/`DELETE`/`INSERT`, no `FOR UPDATE`. Reconciliation is the only writer.
- `test_the_sweep_predicate_can_use_the_state_deadline_index` — the index the
  sweep depends on exists with the right column order.

### 3.8 Redaction cuts secrets and counts drops

- `test_start_writes_only_allow_listed_metadata` — 12 raw keys of which 4 survive
  (allow-listed scalars); `prompt`, `text`, `authorization`, `api_key`,
  `lease_token`, an unknown future key, a nested object and a `None` are dropped.
- `test_start_does_not_double_count_the_drops_it_inherits` — re-redacting an
  already-clean mapping counts nothing, so the column equals what the record
  already counted.
- `test_start_never_persists_the_lease_token_only_its_digest`.
- `test_finish_reports_its_dropped_keys_as_a_delta` — the caller's count plus this
  transition's drops.
- `test_the_finish_statement_adds_the_delta_to_the_stored_count` — rendered
  `SET metadata_dropped=(agent_run_provider_attempts.metadata_dropped + :delta)`;
  the column is never reset.
- `test_a_finish_through_the_store_writes_no_secret`,
  `test_a_forbidden_key_alone_never_reaches_the_row` — the bound parameters of the
  issued statement contain neither the secret, nor the lease token, nor the prompt.
- `test_every_persisted_metadata_key_is_allowed_and_never_forbidden`,
  `test_non_scalar_and_null_values_are_dropped_not_serialised`,
  `test_metadata_must_be_a_mapping`.

### 3.9 `rowcount == 0` → refusal

- `test_a_finish_whose_row_vanished_is_an_invalid_record` — a cascade delete is
  never read as "it probably worked".
- `test_the_raw_cas_returns_zero_instead_of_raising` — the protocol primitive keeps
  `0` as a value (and issues no follow-up read).
- Lost-race classification, all four branches:
  `..._to_the_same_state_is_already_terminal`,
  `..._to_another_outcome_is_already_terminal` (names the winning state),
  `..._to_unknown_requires_reconciliation`,
  `..._without_any_state_change_is_illegal` (a `0` from a predicate that still
  describes the row means the read and the write disagree — a refusal, never a
  silent success).
- `test_the_lost_race_read_is_tenant_narrowed` — a cross-tenant row reads as
  absent, so the refusal stays `INVALID_RECORD` instead of leaking a foreign state.

## 4. Fail-closed cases beyond the required list

| Case | Test |
|---|---|
| Every operation turns a missing table into `AttemptJournalUnavailable` naming `agent_run_provider_attempts` **and** revision `2026110311` | `test_every_operation_reports_the_missing_table_as_a_named_blocker`, `test_a_missing_table_is_a_named_blocker_not_a_crash` |
| An unrelated `ProgrammingError` is re-raised unchanged (no relabelling) | `test_an_unrelated_programming_error_is_not_relabelled` |
| The store can be handed to `ProviderAttemptJournal` (which rejects a port without `cas_finish`) | `test_the_application_journal_accepts_the_store_as_its_port` |
| The store holds no state: the only attribute is the session factory | `test_the_store_holds_no_in_memory_state` |
| No module-level cache of attempts (no process-memory journal) | `test_no_module_level_attribute_remembers_an_attempt` |
| An identity wider than its column is refused before the insert (the contract allows 96 characters, the columns allow 40) | `test_the_widths_the_store_enforces_are_the_column_widths`, `test_an_identity_wider_than_its_column_is_refused_before_the_insert` |
| A corrupt row is `INVALID_RECORD` on read/scan, not a half-trusted object | `test_read_refuses_a_corrupt_row_*`, `test_a_corrupt_unresolved_row_is_a_refusal_not_a_blocker`, `test_a_row_with_an_unknown_state_is_refused`, `test_a_row_that_breaks_a_positive_counter_is_refused` |
| A failure code longer than the 64-char column is refused (contract allows 128) | `test_a_failure_code_longer_than_the_column_is_refused` |
| A success transition cannot carry a failure code / a failure needs one | `test_a_success_transition_cannot_carry_a_failure_code`, `test_a_failure_transition_requires_a_failure_code`, `test_a_free_text_failure_code_is_refused` |
| The insert/finish/read builders refuse a non-record argument before SQL | `test_start_refuses_a_record_that_is_not_freshly_started`, `test_start_refuses_a_non_attempt_value`, `test_finish_requires_a_journal_record` |
| `metadata_dropped` cannot be negative or a bool | `finish_values` validation (exercised through the transition artifacts) |
| The table the store writes is the row the persistence slice authored | `test_the_table_is_the_row_the_persistence_slice_authored`, `test_the_attempt_fk_cascades_with_the_run`, `test_the_named_checks_and_indexes_exist`, `test_the_state_vocabulary_matches_the_contract`, `test_the_store_targets_the_authored_table_and_revision` |

## 5. Statement-shape guarantees (`test_journal_contract.py`)

- `start_insert_statement` — one `INSERT INTO agent_run_provider_attempts`, no
  `UPDATE`/`DELETE`, `RETURNING attempt_id` (the arbiter is the unique
  `idempotency_key`, not a read-then-write).
- `finish_statement` — exactly one `UPDATE`, no `INSERT`/`DELETE`, `WHERE` on
  `attempt_id` **and** the observed `state`.
- `read_statement`, `unresolved_statement`, `expired_unresolved_statement` — one
  `SELECT` each.
- `test_no_statement_touches_a_second_table` — no statement names `agent_runs`,
  `agent_run_leases` or `agent_control_plane_*`: the journal is one table and one
  source of truth.

Rendering note: the `metadata` column is `JSONB`, and SQLAlchemy has no literal
renderer for it (`CompileError`). The artifacts therefore render `WHERE` clauses
with `literal_binds` and review `SET` clauses by column/keyword — hence
`rendered_sql(..., literals=False)` plus `rendered_where(...)` in
`acceptance_fixtures.py`. That split is deliberate: every predicate this store
relies on lives in `WHERE`, and every value written is asserted on the builder
(`start_values` / `finish_values`) or on `compile().params`.

## 6. Concurrency note (why the two-step conflict path is safe)

`INSERT ... ON CONFLICT (idempotency_key) DO NOTHING` blocks on a concurrent
insert of the same key and, when that transaction commits, returns **no** row.
The losing worker then reads the winner inside the *same* transaction. Under
`READ COMMITTED` (the platform default) each statement takes a fresh snapshot, so
the committed winner is visible to that read. The conflict path is therefore one
insert + one read and can never observe "key taken but row invisible" — that
residual branch exists only to raise `INVALID_RECORD` rather than return
`created=True` on a phantom.

## 7. Deliberate gaps carried as blockers (not hidden)

1. **No production wiring yet.** Nothing constructs this store in a delivered
   process: the worker takes a `ProviderAttemptJournal(port=...)` and composition
   still passes `None`, so the host keeps reporting
   `agent_run_provider_attempt_journal_unwired` until a wiring slice builds
   `SqlAlchemyProviderAttemptJournal(session_factory=...)` from the same session
   factory as `agent_runs`. That slice is outside this zone
   (`core/infra`, `application/agent_runs/**` are read-only here).
   `ensure_ready()` exists for exactly that wiring step.
2. **`ATTEMPT_JOURNAL_WRITE_ATOMICITY` is unchanged.** The journal row and the
   run's terminal transition still cannot share a transaction through the current
   port (`cas_finish` takes no session). The window is documented in
   `attempts.py`; closing it needs a port method that accepts a caller-supplied
   transaction, which is a contract change and therefore out of this zone.
3. **No RLS policy on `agent_run_provider_attempts`.** Consistent with the
   documented gap for `agent_runs`/`agent_control_plane_*` (live callers do not
   all set the tenant GUC). Tenancy on this store is enforced by the optional
   `tenant_id` narrow plus the caller supplying it, not by the database.
4. **The sweep is bounded at 200 rows per call.** A reconciler loop must page by
   re-calling with a fresh `now`; there is no cursor, because the predicate is
   self-advancing (each pass either resolves rows or re-reads the same oldest
   ones). Documented rather than silently truncating.
5. **`find_unresolved` answers for one run.** There is no "all runs of a tenant"
   variant, because no caller needs one yet; adding it would put an unbounded
   scan behind the `ix_agent_run_attempts_tenant_started` index and should be a
   deliberate decision.

## 8. Not run

`pytest`, `vitest`, builds, `tsc`, compile, lint, format, `alembic upgrade`, DDL,
database access and every git command were intentionally **not** executed for this
artifact. Only native file tools plus read-only `python -c` / scripted imports
(model, statement and pure-builder metadata) were used; no probe script was left
behind.
