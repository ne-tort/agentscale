# MIG-RES-APPLYPORT-01 acceptance artifact

Durable apply port for the Resource API. Artifacts §1–§6 were authored during the
migration verification freeze and are checkable statements about code and contracts;
§7 records the effect-port wiring follow-up and the executed run.

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
| `resource_effect` | **not a gate requirement** — it records what the *wired* effect boundary still cannot do: `env`/`mcp`/`scope` are refused per operation (`resource_effect_<kind>_unsupported`) and there is no rollback effect (no workspace-byte snapshot to restore) | runtime materialization / pod runtime zone |

`test_optional_evidence_requirements_refuse_when_switched_on` and
`test_gate_accepts_satisfied_optional_evidence` prove the checks are real rather than
promised; `test_every_unproven_evidence_kind_is_documented_with_an_owner` keeps them
from becoming silent.

## 6. Known limits recorded honestly

1. **Populated plans are now applicable** — `ResourceApplyResource.startup` wires
   `durable/workspace_effects.py` through `effects=` (MIG-RES-EFFECT-WIRING-01, see
   §7). `prompt`/`file` operations write through the Pod workspace port. A composer
   caller that passes no `effects` still gets the executor's
   `resource_effect_port_missing`, which is the documented fail-closed default of
   `build_resource_materialization_apply_port` — wiring lives in the lifespan
   resource, not in the composer.
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

## 7. MIG-RES-EFFECT-WIRING-01 — effect port wired, and an executed run

`PodWorkspaceOperationEffectPort` existed but nothing passed it to
`build_resource_materialization_apply_port`, so every non-empty plan still answered
`BLOCKED resource_effect_port_missing`. Now:

| Claim | Mechanism | Artifact |
|---|---|---|
| Startup publishes a real effect boundary | `build_effect_port` → `build_pod_workspace_effect_port(session_factory=…)` → `build_apply_port_provider(effects=…)` | `test_startup_wires_a_real_pod_workspace_effect_port`, `test_provider_composes_the_real_effect_port_when_none_is_injected` |
| The adapter resolves runtimes through *this* process's factory | `SqlAlchemyProjectRuntimeRefResolver(session_factory=…)` identity | `test_startup_binds_the_project_runtime_resolver_to_the_process_factory` |
| No rollback capability is claimed | `executor.rollback_effects is None`; `rollback_supported is False` | `test_startup_leaves_rollback_effects_unwired` |
| Composition failure ⇒ **named** per-apply refusal, never "no effects" | `UnavailableEffectPort`: `resource_effect_port_unavailable`, `effect_unknown=True`, fatal even for `required=False`, receipt bound to the real operation digest + exact fence | `test_effect_composition_failure_publishes_a_named_refusal_port`, `test_unavailable_effect_port_refuses_every_operation[True/False]`, `test_unavailable_effect_port_refuses_compensation_too` |
| The degraded state is observable, without leaking the error text | `effects_degraded` / `effects_error` (exception *type* only; a message carrying a fake DSN never appears) | same |
| The low-level composer keeps the fail-closed default | no `effects` ⇒ `executor.effects is None` | `test_the_low_level_composer_still_refuses_a_populated_plan_without_effects` |
| `env`/`mcp`/`scope` stay deliberately unsupported through the wired port | `resource_effect_<kind>_unsupported`, `effect_unknown=False` | `test_the_wired_boundary_still_refuses_unsupported_kinds[env/mcp/scope]` |
| Flag off still publishes nothing | `materialization_apply_port(...) -> None`, `health() is None` | `test_flag_off_publishes_nothing_even_with_a_working_effect_port` |
| Shutdown withdraws the wired port | `install_materialization_apply_port(None)` | `test_shutdown_withdraws_the_wired_port` |
| The stale blocker claim is gone; the other three are untouched | `EVIDENCE_AUTHORITY_BLOCKERS` | `test_the_stale_no_adapter_claim_is_gone_from_the_blockers`, `test_the_other_blockers_are_untouched` |

The `resource_effect` entry was **reworded, not deleted**: it no longer says "no
Pod/workspace effect adapter exists" (false now) and instead records the remaining,
still-true gap (`env`/`mcp`/`scope` unsupported; no rollback effect) under the same
owner, so the gap keeps its name instead of silently disappearing.

The verification freeze was lifted for this task by explicit instruction, so this
section is a **record of a passed run**:

```
python -m pytest tests/migration/resource_effects tests/migration/resource_apply_port -q
160 passed, 9 skipped
```

The 9 skips are the pre-existing PostgreSQL-gated module
(`test_durable_apply_semantics_db.py`, requires `DATABASE_URL` + revision
`2026110306`); they skip on this machine before and after the change.
