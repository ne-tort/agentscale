# MIG-RES-APPLYPORT-01 acceptance artifact

Durable apply port for the Resource API. **Artifacts only** — nothing here was
executed during the migration verification freeze; every claim below is a checkable
statement about code and contracts, not a record of a passed run.

## 1. Gap this task closes

`application/resources/adapters/factory.py` resolves the CAS boundary through
`materialization_apply_port(session)`, which returned `None` until some process-level
caller invoked `install_materialization_apply_port(provider)`. Nobody did, so
`POST .../materialization/apply` answered **`503 RESOURCE_APPLY_UNAVAILABLE`** for
every request while every CRUD path worked against real tables.

The publisher now exists: `core/infra/resource_apply_resource.py` installs the port
at startup and withdraws it at shutdown, and it is registered in `core/wiring.py`
immediately after the database resource.

## 2. Flag

| | |
|---|---|
| Field | `resource_apply_enabled`, pydantic `bool`, **default `False`** |
| Env | `RESOURCE_APPLY_ENABLED`, alias `RESOURCE_MATERIALIZATION_APPLY_ENABLED` (`AliasChoices`) |
| Second flag | `resource_apply_require_install` (default `True`): an enabled flag that cannot compose the port refuses startup instead of degrading back to 503 |
| Off ⇒ | nothing installed; `materialization_apply_port(...) -> None`; apply stays `503`; CRUD unchanged |
| On ⇒ | `install_materialization_apply_port(<durable provider>)`; shutdown calls `install_materialization_apply_port(None)` |

The flag lives in `core/infra/resource_apply_resource.py` (as a `BaseSettings`
subclass) rather than `config/settings.py`, because that module is outside this
task's file zone. `resource_apply_enabled()` prefers a canonical
`settings.resource_apply_enabled` when its owner adds the field, so promotion there
needs no change here.

## 3. What the port guarantees, and where each guarantee is proven

| Guarantee | Mechanism | Artifact |
|---|---|---|
| Idempotency by key; a repeat returns the original receipt | key derived by `apply_idempotency_key(project, generation, snapshot, operation set)`; replay resolved **before** CAS, returns the stored attempt | `test_repeat_with_the_same_key_returns_the_stored_receipt`, `test_plan_identity_is_derived_only_from_the_plan_inputs` |
| Changed plan is a conflict, never an overwrite | changed digest ⇒ another key **and** a stale observed generation ⇒ head CAS refuses; the stored row is never rewritten | `test_changed_plan_after_a_successful_apply_is_a_conflict`, `test_changed_plan_at_a_consumed_generation_conflicts` |
| Resource-wide CAS, **empty plans included** | the fence is `runtime_revision_heads.generation`, not `materialization_entries` | `test_empty_plan_is_fenced_by_the_head_generation`, `test_empty_plan_advances_the_head_and_writes_attempt_and_audit` |
| Audit in the same transaction; an audit failure rolls everything back | `started`/`finished` rows written with the attempt, head and snapshot in one `session.begin()` | `test_audit_failure_rolls_back_generation_attempt_and_head` |
| `BLOCKED`/`CONFLICT` grant no authority | mapped straight through; neither advances a generation | `test_blocked_and_conflict_grant_no_generation` |
| Late (stale-generation) plan is refused | head CAS answers `CONFLICT stale_generation_cas` | `test_late_plan_with_an_older_generation_is_refused` |
| Rollback creates a new monotonic generation | executor claims `generation + 1` on the head, links `rollback_of_generation` | `test_rollback_claims_the_next_generation_and_links_its_target` |
| Rollback approval must be *provable*, not merely present | the port verifies `approval_requests.status == approved`, `decided_at`, expiry, same project, before any claim | `test_rollback_requires_a_provable_approval`, `test_rollback_refuses_a_non_approved_decision` |
| Fail-closed on missing collaborators | no effect port ⇒ populated plan `BLOCKED resource_effect_port_missing`; unreadable evidence ⇒ `BLOCKED`; ledger crash ⇒ redacted `BLOCKED`, never a 5xx | `test_populated_plan_without_an_effect_port_is_blocked`, `test_gate_refuses_when_evidence_cannot_be_read`, `test_ledger_failure_is_a_redacted_block_not_an_exception` |

## 4. Negative and boundary coverage

* malformed request shapes (`project_id` blank/whitespace, `expected_generation` 0 or
  `True`, non-sha256 digests, untyped operations) are `BLOCKED` **before** the ledger
  is touched — `test_malformed_requests_are_blocked_before_the_ledger`;
* untyped and duplicate-target operation sets are rejected by the plan contract —
  `test_operations_must_be_typed_resource_operations`,
  `test_duplicate_targets_are_rejected_by_the_plan_contract`;
* every grounding predicate has its own refusal code (resource missing / not active /
  quarantine / archived, active version missing or drifted, version missing / draft /
  retired, binding missing or on another version, provenance version/target mismatch,
  operation without resource provenance) —
  `test_gate_refuses_an_operation_it_cannot_ground`,
  `test_gate_refuses_operations_without_resource_provenance`;
* a plan that **skips** generations is refused by the gate (`generation_gap`) while a
  merely **late** plan is left to the CAS, so the caller learns it lost a race instead
  of receiving a generic block —
  `test_gate_reports_a_generation_gap_but_lets_the_cas_report_a_late_plan`;
* evidence resolved for another project cannot be re-aimed at this plan —
  `test_gate_refuses_a_plan_whose_resource_key_was_not_derived`;
* the gate refuses a timezone-naive apply time instead of assuming UTC —
  `test_gate_requires_timezone_aware_time`;
* the ledger crash path never leaks the exception text into diagnostics —
  `test_ledger_failure_is_a_redacted_block_not_an_exception`.

## 5. Evidence that is required versus evidence that does not exist yet

`ResourceApplyEvidencePolicy.for_resource_api()` requires only what the durable
schema can prove: full resource grounding and single-step generation advance. Four
kinds are implemented and switchable but **off**, each with a named owner in
`EVIDENCE_AUTHORITY_BLOCKERS`:

| Kind | Why it is off | Owner |
|---|---|---|
| `bridge_lease` | no durable publisher writes an opaque `lease://` reference with an expiry/epoch per project | runtime materialization / pod bridge zone |
| `approval_link` | `ApplyPlan` carries no approval identity, and a CRUD-triggered apply has no agent run to point at; requiring it would refuse every legitimate apply | Resource API command layer |
| `event_link` | `platform_events` rows are optional for resource commands | `application/resources/api` |
| `resource_effect` | no Pod/workspace adapter implements `ResourceOperationEffectPort` | workspace / pod runtime zone |

`test_optional_evidence_requirements_refuse_when_switched_on` and
`test_gate_accepts_satisfied_optional_evidence` prove the checks are real rather than
promised; `test_every_unproven_evidence_kind_is_documented_with_an_owner` keeps them
from becoming silent.

## 6. Known limits recorded honestly

1. **Populated plans are refused in production** until a `ResourceOperationEffectPort`
   exists: the executor answers `resource_effect_port_missing` instead of
   acknowledging a write that did not happen. Empty plans and the whole
   attempt/head/audit/receipt path work today.
2. **No bridge-lease authority** exists, so `require_bridge_lease` stays off. Turning
   it on without a publisher makes every apply refuse — correct, but not deployable.
3. **No approval linkage** on `ApplyPlan`; approval is enforced where it is claimed
   (rollback) and gated by policy elsewhere.
4. The DB-gated module reuses one outer transaction with
   `join_transaction_mode="create_savepoint"` so an executor rollback (the audit
   failure case) stays observable; it is **not** executed during the freeze.
5. `test_wiring_registers_resource_apply_after_the_database` builds the lifespan
   manager, which constructs manager objects but starts no resource — no network, no
   DB, no Pod call.

Formal tests/build/lint/format/compile/git checks are intentionally not run per the
verification freeze.
