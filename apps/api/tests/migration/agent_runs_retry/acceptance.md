# MIG-AR-RETRY-01 acceptance artifacts (authored, not executed)

Closes readiness blocker №5 from `apps/api/tests/api/agent_runs/readiness_blockers.md`:
*no durable retry scheduler exists and an unknown provider outcome must never be
blind-retried.*

Implementation:

| Module | Responsibility |
|---|---|
| `application/agent_runs/retry.py` | classification, policy, durable `retry_at` journal, `SKIP LOCKED` due dispatch |
| `application/agent_runs/reconcile.py` | unknown-outcome cases, authoritative resolution, quarantine |

Verification freeze: the tests below were written to be executed later. No test,
build, lint, formatter, compile, migration or git command was run while authoring.

## Coverage matrix

### 1. A terminal error is never retried

| Vector | Test |
|---|---|
| 401/403/422/404/409 and `permanent` classify as `terminal` | `test_retry_classification_and_dispatch.py::test_terminal_errors_are_never_retryable` |
| scheduling a terminal failure raises `AgentRunRetryRefused("agent_run.retry.terminal")` and writes no row | `::test_terminal_error_is_refused_and_writes_nothing` |
| attempt ceiling and unreachable deadline exhaust instead of scheduling | `::test_attempt_ceiling_exhausts_the_retry_budget`, `::test_deadline_that_cannot_be_met_exhausts_instead_of_scheduling` |

### 2. An unknown outcome is never scheduled

| Vector | Test |
|---|---|
| `UNKNOWN` classifies as `reconcile_required` | `::test_unknown_and_ambiguous_outcomes_require_reconciliation_not_retry` |
| `schedule_retry` refuses with `agent_run.retry.reconcile_required`, journal untouched | `::test_unknown_outcome_is_refused_and_writes_nothing` |
| a timeout/connection failure after the provider accepted the request is ambiguous | `::test_ambiguous_timeout_with_provider_request_id_is_refused`, `::test_only_proven_never_sent_or_explicit_rejection_permits_a_retry` |
| the worker-result entry point re-asserts `ProviderOutcome.UNKNOWN` is unschedulable | `::test_unknown_worker_result_cannot_be_scheduled` |
| the provider-result contract still rejects `unknown` + `retry_at` | `execution.py::ProviderExecutionResult.__post_init__` (unchanged) |

### 3. An expired backoff is picked up

| Vector | Test |
|---|---|
| a row whose `retry_at` is still in the future is not claimed | `::test_backoff_that_has_not_expired_is_not_claimed` |
| a due row is claimed, released once, and never released twice | `::test_expired_backoff_is_claimed_and_released_exactly_once` |
| an expired claim lease makes a lost retry due again (`claim_generation` increments) | `::test_claim_lease_expiry_makes_a_lost_retry_due_again` |
| deferral keeps the claim until its lease expires, then retries | `::test_deferred_claim_stays_claimed_until_its_lease_expires` |
| a terminal run cancels the schedule instead of requeueing it | `::test_terminal_run_cancels_the_schedule_instead_of_requeueing` |

### 4. Repeat reconciliation does not duplicate a terminal transition

| Vector | Test |
|---|---|
| second `reconcile_case` returns `ALREADY_RESOLVED`, no probe, no transition, no second audit | `test_reconcile_unknown_outcomes.py::test_second_reconcile_of_a_resolved_case_creates_no_second_transition` |
| a due scan never reclaims a resolved or quarantined case | `::test_due_scan_never_reclaims_a_resolved_or_quarantined_case` |
| a crash between decision and transition resumes with the *same* deterministic transition key; the durable write replays as duplicate | `::test_crash_between_decision_and_transition_resumes_with_the_same_command` |
| a run already terminal in another state is superseded, not flipped a second time | `::test_run_already_terminal_supersedes_the_case_without_a_second_status` |
| a blocked durable transition raises and leaves the case resumable (fail-closed) | `::test_blocked_transition_raises_and_leaves_the_case_resumable` |
| a lost resolution race is reported, never claimed as success | `::test_lost_resolution_race_is_reported_not_claimed_as_success` |

### 5. Cross-tenant selection is impossible

| Vector | Test |
|---|---|
| a foreign tenant is rejected before any journal call | `::test_foreign_tenant_is_rejected_before_any_journal_call` |
| a journal row claiming another tenant is rejected | `::test_journal_returning_another_tenant_is_rejected` |
| dispatch scans exactly one tenant; peers stay `pending` | `::test_dispatch_scans_only_the_requested_tenant` |
| a batch containing a foreign claim aborts before any run is requeued | `::test_dispatch_rejects_a_claim_from_another_tenant` |
| reconciliation cannot scan a foreign tenant | `::test_foreign_tenant_cannot_scan_or_address_a_case` |
| a batch containing a foreign case aborts before any run transition | `::test_journal_returning_a_foreign_case_aborts_the_pass` |
| the SQL adapters filter `tenant_id` inside the `SKIP LOCKED` predicate | `test_structural_contract.py::test_due_selection_uses_skip_locked_in_both_adapters` |

### 6. The scheduler loses no work when it dies between claim and write

| Vector | Test |
|---|---|
| claim is persisted before the follow-up write; a crash leaves `claimed` with a live lease and the next pass reclaims it | `::test_claim_lease_expiry_makes_a_lost_retry_due_again` |
| the schedule row is written before the run is requeued (documented in `retry.py`) so `retry_at` survives the crash | `retry.py` module docstring + `insert_schedule_once` before `RetryReleasePort.release` |
| a claim is never deleted, only transited `pending → claimed → released/cancelled` | `::test_expired_backoff_is_claimed_and_released_exactly_once`, `::test_terminal_run_cancels_the_schedule_instead_of_requeueing` |
| more claims than requested is treated as an adapter fault, not processed | `::test_journal_returning_more_claims_than_requested_fails_closed` |

### 7. Unknown-outcome reconciliation

| Vector | Test |
|---|---|
| proven success → `succeeded` with evidence handle + digest | `::test_proven_success_moves_the_run_to_succeeded_with_evidence` |
| proven failure → `failed` with a redacted failure code | `::test_proven_failure_moves_the_run_to_failed_with_a_redacted_code` |
| proven never-applied → retry-eligible, **no** run transition | `::test_not_applied_is_retry_eligible_and_never_transitions_the_run` |
| inconclusive probe → deferral with backoff; after the cap → `quarantined` and never scanned again | `::test_inconclusive_probe_defers_with_backoff_and_quarantines_after_the_cap` |
| a failing source is unresolved, never success | `::test_probe_error_is_treated_as_unresolved_never_as_success`, `::test_missing_source_record_is_not_a_resolution` |
| contradicting evidence quarantines instead of picking a side | `::test_contradicting_evidence_quarantines_instead_of_choosing_a_side` |
| future-dated evidence is unusable | `::test_future_dated_evidence_is_rejected_as_unusable` |
| an expired resolution deadline quarantines | `::test_resolution_deadline_quarantines_an_unresolved_case` |
| registration is idempotent per attempt | `::test_unknown_case_registration_is_idempotent_per_attempt` |
| an unwired boundary fails closed | `::test_unwired_reconciler_fails_closed` |

### 8. Redaction, determinism, durability hygiene

| Vector | Test |
|---|---|
| no `sleep`/spin loop in either production module | `test_structural_contract.py::test_modules_never_sleep_or_spin` |
| no ORM metadata or DDL leaks into the application layer | `::test_application_modules_register_no_orm_metadata_and_no_ddl` |
| audit payload carries codes/digests only | `::test_audit_payload_has_no_payload_or_message_fields` |
| blocker and reason codes are namespaced constants | `::test_blocker_codes_are_distinct_and_stable`, `::test_reason_codes_are_redacted_namespaced_constants` |
| adapters fail closed on a missing table | `::test_sql_adapters_fail_closed_before_touching_schema` |
| classification is stable for a replayed decision (jitter keyed by attempt identity) | `::test_backoff_is_capped_exponential_and_stable_across_replays` |

## Deliberately not covered here

* **DDL / migration.** Both modules address their tables through metadata-free
  `TableClause` objects and own no schema. The SQL adapters need tables
  `agent_run_retry_schedules`, `agent_run_unknown_outcomes` and
  `agent_run_reconciliation_audit`; their column contract is published as
  `RETRY_SCHEDULE_REQUIRED_COLUMNS` / `UNKNOWN_OUTCOME_REQUIRED_COLUMNS` /
  `RECONCILIATION_AUDIT_REQUIRED_COLUMNS`. Until an Alembic revision exists the
  adapters raise `agent_run_retry_schedules_table_missing` /
  `agent_run_unknown_outcomes_table_missing` (fail-closed, no in-memory fallback).
* **Real PostgreSQL concurrency.** `SKIP LOCKED`, lease CAS under two writers and
  the unique `schedule_key`/`reconcile_key` constraints are asserted structurally
  and via contract doubles; live-database coverage belongs to the verification
  window (blocker №6).
* **Host wiring.** `AgentRunRetryScheduler`/`AgentRunUnknownOutcomeReconciler` are
  not constructed by any worker yet — `worker.py` and `dispatcher.py` are outside
  this task's file zone. Blockers reported: `agent_run_retry_journal_unwired` and
  `agent_run_unknown_outcome_journal_unwired`.
