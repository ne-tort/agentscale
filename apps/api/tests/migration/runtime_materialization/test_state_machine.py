"""MIG-RES-02 acceptance fixtures. Authored only; migration freeze forbids running."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from prodavan.application.migration.runtime_materialization.state_machine import (
    ApplyClaim,
    ApplyOutcome,
    ClaimResult,
    ClaimStatus,
    DependencyEvidence,
    DesiredRevision,
    MaterializationOperation,
    OperationReceipt,
    OperationValidation,
    OperationAction,
    PlannedOperation,
    PlanAction,
    Provenance,
    RevisionPhase,
    RollbackCommand,
    RuntimeMaterializationService,
    RuntimeRevisionState,
    RuntimeDiagnostic,
    RuntimeClockPort,
    _make_plan,
)

IMAGE = "sha256:" + "a" * 64
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def provenance():
    return Provenance("agent_run", "run-fixture-1", "rev-8", "corr-fixture-1")


@pytest.fixture
def evidence():
    return DependencyEvidence(
        bridge_lease_ref="lease://bridge/lease-42", image_digest=IMAGE,
        projected_secret_ref="vault://tenant/runtime",
        agent_run_id="run-42", approval_id="approval-42", event_id="event-42",
        contracts_ready=True, bridge_lease_expires_at=NOW + timedelta(minutes=5),
        bridge_lease_generation=4,
    )


@pytest.fixture
def operation():
    return MaterializationOperation("op-runtime-config", "project/runtime/config", {"replicas": 1})


def desired(provenance, evidence, operation, *, revision=1, owner="owner-7", image=IMAGE):
    return DesiredRevision("project/runtime-7", revision, {"image_digest": image}, provenance, evidence,
                           operations=(operation,), owner_id=owner)


class FakeStore:
    def __init__(self):
        self.state = None
        self.attempts = {}
        self.history = {}
        self.claim_count = 0
        self.finish_count = 0
        self.claim_override = None
        self.fail_finish = False

    async def load_state(self, *, resource_key):
        return self.state if self.state and self.state.resource_key == resource_key else None

    async def find_attempt(self, *, resource_key, idempotency_key):
        return self.attempts.get((resource_key, idempotency_key))

    async def load_applied_revision(self, *, resource_key, generation):
        return self.history.get((resource_key, generation))

    async def claim(self, *, plan, expected_generation):
        self.claim_count += 1
        if self.claim_override is not None:
            return self.claim_override
        observed = self.state.generation if self.state else 0
        if observed != expected_generation:
            return ClaimResult(ClaimStatus.CONFLICT, reason_code="generation_conflict")
        claim = ApplyClaim(plan.resource_key, expected_generation + 1, "opaque-fence-9",
                           plan.idempotency_key, plan.plan_digest)
        return ClaimResult(ClaimStatus.CLAIMED, claim=claim)

    async def finish(self, *, claim, state, attempt):
        self.finish_count += 1
        if self.fail_finish:
            return False
        observed = self.state.generation if self.state else 0
        if observed != claim.generation - 1:
            return False
        self.state = state
        self.attempts[(attempt.resource_key, attempt.idempotency_key)] = attempt
        if attempt.applied_revision is not None:
            self.history[(state.resource_key, state.generation)] = attempt.applied_revision
        return True


class FakeValidator:
    def __init__(self, not_ready=None):
        self.not_ready = not_ready or set()
        self.calls = []

    async def validate_operation(self, *, resource_key, operation, manifest, prior_manifest, evidence, expected_generation):
        operation_id = operation.operation.operation_id
        self.calls.append(operation_id)
        return OperationValidation(operation_id, operation_id not in self.not_ready,
                                   "target_unavailable" if operation_id in self.not_ready else "ready")


class FakeRuntime:
    def __init__(self, failures=None, *, unknown=None):
        self.failures = failures or {}
        self.unknown = unknown or set()
        self.applied = []
        self.compensated = []
        self.calls = []
        self.receipt_fence_override: str | None = None

    async def apply_operation(self, *, planned, manifest, prior_manifest, evidence, claim):
        operation_id = planned.operation.operation_id
        self.calls.append(operation_id)
        ok = operation_id not in self.failures and operation_id not in self.unknown
        reason = self.failures.get(operation_id, "runtime_effect_unknown" if operation_id in self.unknown else "applied")
        if ok:
            self.applied.append(operation_id)
        return OperationReceipt(operation_id, planned.digest, manifest.owner_id,
                                self.receipt_fence_override or claim.fence_token,
                                claim.generation, ok, reason,
                                operation_id in self.unknown)

    async def compensate_operation(self, *, planned, prior_manifest, evidence, claim):
        operation_id = planned.operation.operation_id
        self.compensated.append(operation_id)
        return OperationReceipt(operation_id, planned.digest, "owner-7", claim.fence_token,
                                claim.generation, True, "compensated")


class FakeAudit:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.records = []

    async def append(self, *, record):
        if self.fail:
            raise RuntimeError("raw adapter error must not enter diagnostics")
        self.records.append(record)


class FakeClock:
    def __init__(self, current=NOW):
        self.current = current

    async def now(self):
        return self.current


class FakeEvidenceValidator:
    def __init__(self, blockers=()):
        self.blockers = tuple(blockers)
        self.calls = []

    async def validate_evidence(self, *, resource_key, desired, at):
        self.calls.append((resource_key, at))
        return self.blockers


@pytest.fixture
def ports():
    store, validator, runtime, audit = FakeStore(), FakeValidator(), FakeRuntime(), FakeAudit()
    service = RuntimeMaterializationService(store=store, validator=validator, runtime=runtime, audit=audit,
                                            evidence_validator=FakeEvidenceValidator(),
                                            clock=FakeClock())
    return store, validator, runtime, audit, service


def run(coro):
    return asyncio.run(coro)


def test_default_dependency_evidence_fails_closed_with_diagnostics(provenance, operation):
    absent = DependencyEvidence()
    item = desired(provenance, absent, operation)
    plan = _make_plan(item, RuntimeRevisionState(item.resource_key), idempotency_key="idem")
    assert plan.action is PlanAction.BLOCKED
    blockers = set(absent.blockers(item, at=NOW))
    assert {"upstream_resource_contract_missing", "bridge_lease_missing", "bridge_lease_generation_missing",
            "bridge_lease_expiry_missing", "immutable_image_digest_missing", "agent_run_link_missing",
            "approval_link_missing", "event_link_missing"} <= blockers


def test_desired_payload_is_frozen_and_digest_binds_provenance(provenance, evidence, operation):
    source = {"image_digest": IMAGE, "nested": {"values": [1, 2]}}
    revision = DesiredRevision("project/runtime-7", 1, source, provenance, evidence, operations=(operation,))
    source["nested"]["values"].append(3)
    assert revision.spec["nested"]["values"] == (1, 2)
    with pytest.raises((AttributeError, TypeError)):
        revision.spec["nested"]["values"] = ()
    changed = replace(revision, provenance=replace(provenance, source_revision="rev-9"))
    assert changed.digest != revision.digest


def test_secret_values_rejected_and_external_refs_require_projection(provenance, evidence):
    with pytest.raises(ValueError):
        DesiredRevision("resource/secret", 1, {"api_token": "literal-secret"}, provenance, evidence)
    secure = DesiredRevision("resource/secret", 1,
        {"image_digest": IMAGE, "database_password_ref": "vault://tenant/db"}, provenance, evidence)
    assert "projected_secret_reference_missing" in evidence.blockers(secure, at=NOW)
    projected = replace(evidence, projected_secret_refs=("vault://tenant/db",))
    secured = replace(secure, evidence=projected)
    assert not projected.blockers(secured, at=NOW)
    assert "literal-secret" not in repr(secured)


def test_digests_manifest_and_order_are_deterministic(provenance, evidence):
    first_op = MaterializationOperation("z-op", "target/z", {"v": 1})
    second_op = MaterializationOperation("a-op", "target/a", {"v": 2})
    left = DesiredRevision("resource/d", 1, {"image_digest": IMAGE}, provenance, evidence,
                           operations=(first_op, second_op))
    right = DesiredRevision("resource/d", 1, {"image_digest": IMAGE}, provenance, evidence,
                            operations=(second_op, first_op))
    p1 = _make_plan(left, RuntimeRevisionState("resource/d"), idempotency_key="idem")
    p2 = _make_plan(right, RuntimeRevisionState("resource/d"), idempotency_key="idem")
    assert left.digest == right.digest
    assert p1.plan_digest == p2.plan_digest
    assert p1.desired_manifest.digest == p2.desired_manifest.digest


def test_expired_revoked_and_missing_image_block_before_claim(provenance, operation, ports):
    store, _, runtime, _, service = ports
    invalid = DependencyEvidence("lease://bridge/l", image_digest=None, agent_run_id="r",
        approval_id="a", event_id="e", contracts_ready=True,
        bridge_lease_expires_at=NOW - timedelta(seconds=1), bridge_lease_generation=2, revoked=True)
    prepared = run(service.prepare(desired(provenance, invalid, operation), idempotency_key="blocked"))
    report = run(service.validate(prepared, at=NOW))
    result = run(service.apply(report))
    assert not report.valid and result.outcome is ApplyOutcome.BLOCKED
    assert store.claim_count == 0 and runtime.calls == []


def test_image_evidence_must_match_pinned_digest(provenance, evidence, operation):
    item = desired(provenance, evidence, operation, image="sha256:" + "b" * 64)
    assert "image_digest_evidence_mismatch" in evidence.blockers(item, at=NOW)


def test_prepare_apply_and_exact_replay(provenance, evidence, operation, ports):
    store, validator, runtime, audit, service = ports
    item = desired(provenance, evidence, operation)
    prepared = run(service.prepare(item, idempotency_key="apply-1"))
    assert prepared.plan.next_generation == 1
    assert prepared.plan.desired_manifest.owner_id == "owner-7"
    checked = run(service.validate(prepared, at=NOW))
    result = run(service.apply(checked))
    assert result.outcome is ApplyOutcome.APPLIED
    assert result.applied.generation == 1 and result.applied.image_digest == IMAGE
    assert result.applied.provenance == provenance
    assert result.applied.manifest.digest == prepared.plan.desired_manifest.digest
    assert store.state.phase is RevisionPhase.APPLIED
    replay = run(service.apply(checked))
    assert replay.outcome is ApplyOutcome.REPLAY and replay.applied == result.applied
    assert runtime.calls == [operation.operation_id]
    assert len(audit.records) >= 3 and validator.calls == [operation.operation_id]


def test_noop_does_not_bump_generation_or_call_runtime(provenance, evidence, operation, ports):
    store, _, runtime, _, service = ports
    item = desired(provenance, evidence, operation)
    p1 = run(service.prepare(item, idempotency_key="first"))
    applied = run(service.apply(run(service.validate(p1, at=NOW))))
    runtime.calls.clear()
    p2 = run(service.prepare(item, idempotency_key="noop"))
    assert p2.plan.action is PlanAction.NOOP
    no_op = run(service.apply(run(service.validate(p2, at=NOW))))
    assert no_op.outcome is ApplyOutcome.REPLAY
    assert no_op.applied.generation == applied.applied.generation == 1
    assert runtime.calls == []


def test_stale_generation_conflicts_before_runtime_side_effect(provenance, evidence, operation, ports):
    store, _, runtime, _, service = ports
    item = desired(provenance, evidence, operation)
    prepared = run(service.prepare(item, idempotency_key="fence"))
    checked = run(service.validate(prepared, at=NOW))
    result = run(service.apply(checked))
    assert result.outcome is ApplyOutcome.CONFLICT
    assert store.claim_count == 1 and runtime.calls == []


def test_required_failure_is_write_barrier_and_failed_attempt_is_persisted(provenance, evidence, ports):
    store, _, _, audit, _ = ports
    required = MaterializationOperation("01-required", "target/required", {"v": 1})
    later = MaterializationOperation("02-later", "target/later", {"v": 2})
    runtime = FakeRuntime({"01-required": "target_conflict"})
    service = RuntimeMaterializationService(store=store, validator=FakeValidator(), runtime=runtime, audit=audit,
                                            evidence_validator=FakeEvidenceValidator(), clock=FakeClock())
    item = DesiredRevision("project/runtime-7", 1, {"image_digest": IMAGE}, provenance, evidence,
                           operations=(required, later), owner_id="owner-7")
    result = run(service.apply(run(service.validate(run(service.prepare(item, idempotency_key="fail")), at=NOW))))
    assert result.outcome is ApplyOutcome.FAILED
    assert runtime.calls == ["01-required"]
    assert store.state.phase is RevisionPhase.FAILED and store.state.generation == 1
    assert store.state.diagnostics[0].reason_code == "target_conflict"


def test_optional_failure_is_diagnostic_and_independent_required_work_continues(provenance, evidence, ports):
    store, _, _, audit, _ = ports
    optional = MaterializationOperation("01-optional", "target/optional", {"v": 1}, required=False)
    required = MaterializationOperation("02-required", "target/required", {"v": 2})
    runtime = FakeRuntime({"01-optional": "optional_missing"})
    service = RuntimeMaterializationService(store=store, validator=FakeValidator(), runtime=runtime, audit=audit,
                                            evidence_validator=FakeEvidenceValidator(), clock=FakeClock())
    item = DesiredRevision("project/runtime-7", 1, {"image_digest": IMAGE}, provenance, evidence,
                           operations=(optional, required), owner_id="owner-7")
    result = run(service.apply(run(service.validate(run(service.prepare(item, idempotency_key="optional")), at=NOW))))
    assert result.outcome is ApplyOutcome.APPLIED
    assert runtime.calls == ["01-optional", "02-required"]
    assert result.diagnostics[0].reason_code == "optional_missing"
    assert store.state.phase is RevisionPhase.APPLIED
    retry = run(service.prepare(item, idempotency_key="optional-retry"))
    assert retry.plan.action is PlanAction.RETRY


def test_stale_operation_fence_is_rejected_and_marked_uncertain(provenance, evidence, operation, ports):
    store, _, _, audit, _ = ports
    runtime = FakeRuntime()
    runtime.receipt_fence_override = "old-fence"
    service = RuntimeMaterializationService(store=store, validator=FakeValidator(), runtime=runtime, audit=audit,
                                            evidence_validator=FakeEvidenceValidator(), clock=FakeClock())
    item = desired(provenance, evidence, operation)
    prepared = run(service.prepare(item, idempotency_key="fenced"))
    result = run(service.apply(run(service.validate(prepared, at=NOW))))
    assert result.outcome is ApplyOutcome.FAILED
    assert store.state.uncertain_operations
    assert store.state.diagnostics[0].reason_code == "operation_receipt_or_fence_mismatch"


def test_trusted_evidence_port_rejection_blocks_before_claim(provenance, evidence, operation):
    store, runtime, audit = FakeStore(), FakeRuntime(), FakeAudit()
    service = RuntimeMaterializationService(
        store=store, validator=FakeValidator(), runtime=runtime, audit=audit,
        evidence_validator=FakeEvidenceValidator(("bridge_lease_subject_mismatch",)), clock=FakeClock(),
    )
    prepared = run(service.prepare(desired(provenance, evidence, operation), idempotency_key="trusted-proof"))
    report = run(service.validate(prepared, at=NOW))
    result = run(service.apply(report))
    assert not report.valid
    assert result.outcome is ApplyOutcome.BLOCKED
    assert store.claim_count == 0 and runtime.calls == []


def test_apply_rechecks_lease_after_validation(provenance, evidence, operation):
    store, runtime, audit, clock = FakeStore(), FakeRuntime(), FakeAudit(), FakeClock()
    service = RuntimeMaterializationService(store=store, validator=FakeValidator(), runtime=runtime,
                                            audit=audit, evidence_validator=FakeEvidenceValidator(), clock=clock)
    prepared = run(service.prepare(desired(provenance, evidence, operation), idempotency_key="lease-race"))
    report = run(service.validate(prepared, at=NOW))
    clock.current = NOW + timedelta(minutes=6)
    result = run(service.apply(report))
    assert result.outcome is ApplyOutcome.BLOCKED
    assert result.reason_code == "evidence_stale"
    assert store.claim_count == 0 and runtime.calls == []


def test_unknown_outcome_persists_failed_attempt_and_next_revision_is_retry(provenance, evidence):
    store, audit = FakeStore(), FakeAudit()
    first = MaterializationOperation("op-1", "target/one", {"v": 1})
    uncertain = PlannedOperation(OperationAction.UPSERT, first)
    runtime = FakeRuntime(unknown={"op-1"})
    service = RuntimeMaterializationService(store=store, validator=FakeValidator(), runtime=runtime,
                                            audit=audit, evidence_validator=FakeEvidenceValidator(), clock=FakeClock())
    candidate = DesiredRevision("project/runtime-7", 1, {"image_digest": IMAGE}, provenance, evidence,
                                operations=(first,), owner_id="owner-7")
    prepared = run(service.prepare(candidate, idempotency_key="uncertain"))
    failed = run(service.apply(run(service.validate(prepared, at=NOW))))
    assert failed.outcome is ApplyOutcome.FAILED
    assert store.state is not None
    assert store.state.uncertain_operations
    retry = run(service.prepare(candidate, idempotency_key="retry"))
    assert retry.plan.action is PlanAction.RETRY
    assert retry.plan.next_generation == store.state.generation + 1


def test_failure_entry_point_is_generation_and_idempotency_fenced(provenance, evidence, operation, ports):
    store, _, _, _, service = ports
    candidate = desired(provenance, evidence, operation)
    prepared = run(service.prepare(candidate, idempotency_key="failure-entry"))
    plan = prepared.plan
    claim = ApplyClaim(plan.resource_key, plan.next_generation, "opaque-fence", plan.idempotency_key, plan.plan_digest)
    failed = run(service.failure(desired=candidate, plan=plan, claim=claim,
                                 diagnostics=(RuntimeDiagnostic("adapter", "pod_create_failed"),)))
    assert failed.outcome is ApplyOutcome.FAILED
    assert store.state.phase is RevisionPhase.FAILED
    assert store.attempts[(plan.resource_key, plan.idempotency_key)].outcome is ApplyOutcome.FAILED


def test_audit_failure_denies_before_claim_and_runtime_side_effect(provenance, evidence, operation):
    store, runtime, audit = FakeStore(), FakeRuntime(), FakeAudit(fail=True)
    service = RuntimeMaterializationService(store=store, validator=FakeValidator(), runtime=runtime, audit=audit,
                                            evidence_validator=FakeEvidenceValidator(), clock=FakeClock())
    item = desired(provenance, evidence, operation)
    prepared = run(service.prepare(item, idempotency_key="audit"))
    result = run(service.apply(run(service.validate(prepared, at=NOW))))
    assert result.outcome is ApplyOutcome.BLOCKED and result.reason_code == "audit_write_failed"
    assert store.claim_count == 0 and runtime.calls == []


def test_rollback_is_approved_monotonic_and_keeps_prior_revisions(provenance, evidence, operation, ports):
    store, _, runtime, _, service = ports
    first_item = desired(provenance, evidence, operation, revision=1)
    first = run(service.apply(run(service.validate(run(service.prepare(first_item, idempotency_key="rev1")), at=NOW))))
    changed_op = MaterializationOperation(operation.operation_id, operation.target_ref, {"replicas": 2})
    second_item = desired(provenance, evidence, changed_op, revision=2)
    second = run(service.apply(run(service.validate(run(service.prepare(second_item, idempotency_key="rev2")), at=NOW))))
    assert second.applied.generation == 2
    rolled = run(service.rollback(RollbackCommand("project/runtime-7", 1, 2, "approval-42", "rollback-1"),
                                  evidence=evidence, at=NOW))
    assert rolled.outcome is ApplyOutcome.ROLLED_BACK
    assert rolled.applied.generation == 3 and rolled.applied.rollback_of_generation == 1
    assert store.history[("project/runtime-7", 1)] == first.applied
    assert store.history[("project/runtime-7", 2)] == second.applied


def test_missing_or_mismatched_rollback_approval_is_blocked(provenance, evidence, operation, ports):
    store, _, _, _, service = ports
    item = desired(provenance, evidence, operation)
    run(service.apply(run(service.validate(run(service.prepare(item, idempotency_key="base")), at=NOW))))
    mismatched = replace(evidence, approval_id="different-approval")
    result = run(service.rollback(RollbackCommand(item.resource_key, 1, 1, "approval-42", "rollback"),
                                  evidence=mismatched, at=NOW))
    assert result.outcome is ApplyOutcome.BLOCKED
    assert "rollback_approval_missing_or_mismatched" in {d.reason_code for d in result.diagnostics}


# Adapter-level CAS races, live lease revocation re-check, and uncertain-effect
# reconciliation are required for promotion. Tests use in-memory fakes only;
# no DB, Pod or bridge is accessed. No tests/build/lint/compile/git checks are run.
