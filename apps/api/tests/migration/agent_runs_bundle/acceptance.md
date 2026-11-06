# MIG-AR-BUNDLE-01 — acceptance matrix (artifacts, not executed)

**Task:** assemble and register the dependency bundle of the Agent Run host loops.
**Stage rule:** verification freeze — nothing here is run. Each case is written so it
can be checked against `core/infra/agent_run_bundle.py` and the
`AgentRunHostDependencies` contract in `core/infra/agent_run_worker_resource.py`.

- Implementation: `apps/api/src/prodavan/core/infra/agent_run_bundle.py` (new).
- Artifacts: `apps/api/tests/migration/agent_runs_bundle/` (new).
- Not edited: `core/infra/agent_run_worker_resource.py` — the seam
  `register_host_dependency_provider` is imported, never changed.

## The composition this bundle performs

| Member | Class | Blocked when absent |
|---|---|---|
| `execution_port` | `application/agent_runs/execution_adapter.ControlPlaneAgentRunExecutionAdapter` | **consumer + worker** — `agent_run_execution_port_unwired`. The consumer refuses to claim: claiming without an executor records `intent_handler_not_wired` retries and walks a real handoff to its poison-pill state. |
| `runs` | `infrastructure/persistence/agent_control_plane/repository.SqlAlchemyAgentControlPlaneRepository` | worker — `agent_run_run_input_source_unwired` (`project_id`/`session_id`/`input_payload` unreadable). **Derived:** the adapter cannot exist without it, so `agent_run_execution_port_unwired` follows. |
| `journal` | `application/agent_runs/attempts.ProviderAttemptJournal` over `…/attempt_journal.SqlAlchemyProviderAttemptJournal` | worker — `agent_run_provider_attempt_journal_unwired`. `AgentRunWorker.__init__` refuses to exist without the durable ledger. |
| `runner` | `application/agent_runs/worker.SessionAgentTurnRunner` → `agent.session_service.start_detached_turn` | worker — `agent_run_turn_runner_unwired`. |
| *(not a member)* `lease_store` | `…/agent_control_plane/lease_store.SqlAlchemyRunLeaseStore` | reported as `agent_run_lease_store_unwired`; **derived** `agent_run_execution_port_unwired` (a port without a fence store would claim runs nobody can release). Not a contract field — the four-member dataclass is untouched — but a bundle without it cannot hold a port. |

`AgentRunHostDependencies` has exactly four fields, so the lease store is carried on
the bundle (`AgentRunBundle.lease_store`) and reported separately. Nothing here
re-declares the contract.

## Required coverage

| Requirement | Test file | Cases |
|---|---|---|
| full bundle assembles; `any_wired` True; blockers empty | `test_full_bundle.py` | `test_full_bundle_composes_every_member_from_the_real_classes`, `test_the_journal_member_is_the_application_wrapper_over_the_sql_port`, `test_full_bundle_reports_no_blockers_and_is_wired`, `test_snapshot_is_a_plain_mapping_in_a_fixed_member_order`, `test_dependencies_only_helper_matches_the_bundle`, `test_execution_port_advertises_that_it_is_really_wired`, `test_the_control_plane_factory_is_tenant_bound` |
| the addressable intent path (same parts, different fence) | `test_full_bundle.py` | `test_intent_pipeline_is_wired_from_the_bundle`, `test_intent_pipeline_factory_builds_a_port_over_the_given_store`, `test_registry_executes_the_created_intent`, `test_registry_can_park_non_executable_kinds_terminally`, `test_registry_refuses_to_park_an_executable_kind`, `test_dispatcher_uses_the_addressed_registry_not_the_claim_run_registry`, `test_consumer_lease_seconds_is_delegated_not_re_derived` |
| one-line host composition | `test_full_bundle.py` | `test_host_resource_is_built_with_bundle_dispatcher_and_factory`, `test_host_resource_can_skip_installation_and_stay_unwired` |
| each member absent → its named blocker, `any_wired` by rule | `test_member_absence.py` | `test_missing_execution_port_blocks_both_loops_and_leaves_the_rest_wired`, `test_missing_run_source_also_blocks_the_port_that_needs_it`, `test_missing_journal_blocks_only_the_worker`, `test_missing_turn_runner_blocks_only_the_worker`, `test_missing_lease_store_blocks_the_port_that_would_fence_against_it`, `test_everything_declared_unavailable_is_the_empty_bundle` |
| host behaviour on a partial/empty bundle (no claim, no session) | `test_member_absence.py` | `test_empty_bundle_makes_the_host_idle_without_opening_a_session`, `test_partial_bundle_makes_only_the_incomplete_loop_idle` |
| a refusing constructor is a code, not a crash | `test_member_absence.py` | `test_a_refusing_constructor_becomes_a_composition_code`, `test_a_journal_that_is_not_a_port_is_refused_rather_than_wrapped`, `test_a_port_that_cannot_claim_is_refused`, `test_a_control_plane_that_is_neither_an_instance_nor_a_factory_is_refused`, `test_every_member_seam_accepts_an_injected_factory[4]` |
| install/restore are idempotent and restore the fail-closed default | `test_install_restore.py` | `test_install_publishes_the_built_bundle_on_the_seam`, `test_the_provider_never_rebuilds_on_each_call`, `test_install_is_idempotent_for_the_same_bundle`, `test_a_later_install_replaces_the_previous_bundle`, `test_install_refuses_anything_that_is_not_a_bundle`, `test_restore_returns_the_pristine_fail_closed_default`, `test_restore_is_idempotent`, `test_install_after_restore_works_again`, `test_restored_seam_makes_an_enabled_resource_idle`, `test_installed_seam_is_what_an_enabled_resource_resolves` |
| assembly never raises on an unavailable session factory | `test_unavailable_factory.py` | `test_unresolvable_process_factory_is_a_named_blocker_not_an_exception`, `test_dependencies_only_helper_also_returns_an_empty_bundle`, `test_a_non_callable_session_factory_is_refused_up_front`, `test_an_empty_bundle_can_still_be_installed_and_reported`, `test_a_host_built_from_an_empty_bundle_idles_instead_of_crashing` |
| no message (and no DSN) leaves the composition | `test_unavailable_factory.py` | `test_the_exception_message_never_reaches_the_report_or_the_log`, `test_composition_failure_code_is_reserved_for_real_failures` |

## `any_wired` semantics (the resource's own rule, not a second one)

`AgentRunHostDependencies.any_wired` is `True` when **at least one** of the four
members is assembled; the resource maps `False` to a single
`agent_run_host_dependencies_unwired` for both loops. The bundle never overrides
that rule — `AgentRunBundleReport.any_wired` delegates to the dataclass, and the
stricter `wired` (all four members + lease store + no failed constructor) exists
only for operators, so "the loops can do something" and "the bundle is complete"
cannot be confused.

| Variant | members present | `any_wired` | reported blockers |
|---|---|---|---|
| full | 4 + lease store | `True` | — |
| `control_plane=None` | 3 | `True` | `…_execution_port_unwired` |
| `runs_factory=None` | 2 | `True` | `…_execution_port_unwired`, `…_run_input_source_unwired` |
| `journal_factory=None` | 3 | `True` | `…_provider_attempt_journal_unwired` |
| `runner_factory=None` | 3 | `True` | `…_turn_runner_unwired` |
| `lease_store_factory=None` | 3 | `True` | `…_execution_port_unwired`, `…_lease_store_unwired` |
| everything unavailable | 0 | `False` | all four + `…_lease_store_unwired` |
| process factory raises | 0 | `False` | all four + lease store + `…_session_factory_unavailable` |

## Design decisions a reviewer should judge

1. **Four members, one factory, real classes only.** No in-memory stand-in exists
   anywhere on this path: an unavailable member is `None` and the host names it.
   The bundle adds no second lease store, no second repository (the run read port
   *is* the control-plane repository: it satisfies `AgentControlPlaneReadPort` and
   `RunInputSource` at once) and no second control plane.
2. **The lease store is on the bundle, not in the contract.** `AgentRunHostDependencies`
   has four fields and this task may not change another slice's dataclass. The store
   is needed twice — by the port and by the addressed intent pipeline — so it is
   carried on `AgentRunBundle` and reported as a fifth, clearly-labelled blocker.
3. **`execution_factory`, not a pre-built port, is the reusable part.** An intent
   must execute *one addressed run*; `AgentRunIntentHandler` hands the adapter an
   `IntentAddressedLeaseStore`, so the port has to be built per dispatch. The bundle
   therefore exposes `callable(lease_store) -> port` and the host's own
   `dependencies.execution_port` is the port over the plain store.
4. **The intent dispatcher is composed here, not by
   `intent_consumer.build_intent_dispatcher`.** That builder registers
   `build_execution_handlers(execution)` → `AgentRunExecutionHandler`, which requires
   `port.claim_run`; the fenced adapter has no addressable claim (it documents the
   refusal), so every claimed intent would be retried into the poison-pill bound. The
   bundle composes the same consumer + scope resolver with the *addressed* registry
   (`agent_runs.handler`). This is asserted, not assumed:
   `test_dispatcher_uses_the_addressed_registry_not_the_claim_run_registry` and
   `test_execution_port_advertises_that_it_is_really_wired` (`not hasattr(port, "claim_run")`).
5. **One provider per install, answering with one instance.** The resource resolves
   the provider on start, but an admin tool or a second resource may call it again;
   rebuilding per call would create a second lease store and a second repository in
   one process. `install_*` publishes a closure over the already-built dependencies.
6. **Restore is identity with the pristine default.** `register_host_dependency_provider(None)`
   re-installs `unavailable_host_dependencies` *itself*, so the assertion
   `current_host_dependency_provider() is unavailable_host_dependencies` proves the
   fail-closed default is literally back — not merely equal to it.
7. **Redaction.** Composition errors are the one place a DSN can sit inside an
   exception text, so failures log and report only the exception **class** (as a
   bounded code) plus a fixed code; no `logger.exception`, no message, no traceback.
8. **The consumer lease is delegated, never re-derived.** `consumer_lease_seconds`
   calls the resource's `_consumer_lease_seconds`; two copies of that formula would
   drift into quarantining deliverable intents (one `lease_until` per batch, expired
   `dispatching` rows are quarantined, not retried).

## Deliberately out of scope (blockers for the parent)

1. **`core/wiring.py` is not edited.** The delivered API still registers
   `AgentRunWorkerResource()` bare, so with the flags on it logs
   `agent_run_host_dependencies_unwired`. Making the process use this bundle is a
   two-line change in someone else's zone: import `build_agent_run_host_resource`
   and register its result instead of `AgentRunWorkerResource()`. (The function is
   written for exactly that: it installs the bundle on the seam, composes the
   addressed dispatcher and leaves the loops opt-in.)
2. **The retry and reconciliation journals are not bundle members.**
   `SqlAlchemyAgentRunRetryJournal` and `SqlAlchemyUnknownOutcomeJournal` both exist
   and both take one `session_factory`, but neither is a member of
   `AgentRunHostDependencies` or `IntentExecutionPipeline`; their schedulers have no
   host loop yet. Wiring them here would be dead configuration — and
   `agent_run_unknown_case_journal_unwired` stays a real blocker until the reconcile
   journal is given a loop.
3. **No host loop consumes the intent dispatcher's `execution=` argument.** It is set
   for signature parity; the addressed handler builds its own per-dispatch worker, so
   the argument is not consulted on the run path.
4. **`agent_run_approval_projection_unwired` is untouched.** Non-executable kinds
   stay unregistered (they retry) unless a host passes them in `terminal_kinds`, which
   parks them with a terminal refusal.
