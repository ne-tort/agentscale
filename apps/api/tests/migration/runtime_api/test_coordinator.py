"""MIG-RUNTIME-API-01 contracts. Authored only; execution remains frozen."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from prodavan.application.migration.runtime_materialization.state_machine import (
    AppliedRevision,
    ApplyOutcome,
    ApplyResult,
    AttemptRecord,
    ClaimResult,
    ClaimStatus,
    DependencyEvidence,
    DesiredRevision,
    MaterializationOperation,
    OperationAction,
    PlanAction,
    OperationReceipt,
    OperationValidation,
    OwnershipManifest,
    PlannedOperation,
    Provenance,
    RevisionPhase,
    RuntimeMaterializationService,
    RuntimeDiagnostic,
    RuntimeRevisionState,
)
from prodavan.application.migration.runtime_api import (
    OBSERVED_RUNTIME_API_ENDPOINTS,
    UNSUPPORTED_RUNTIME_API_ENDPOINTS,
    RuntimeApiBridgeLeaseReference,
    RuntimeApiCoordinator,
    RuntimeApiExecutionReceipt,
    RuntimeApiDiagnostic,
    RuntimeApiOutcome,
    RuntimeApiPrepareRequest,
    RuntimeApiProjectBinding,
    RuntimeApiProjectScope,
    endpoint_contract,
)

IMAGE = "sha256:" + "a" * 64
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
SCOPE = RuntimeApiProjectScope("project-7", "company-2", "cabinet-3", "pod-8", "project/runtime-7")
LEASE = RuntimeApiBridgeLeaseReference(
    "bridge-lease://pod-8/lease-42", "project-7", "pod-8", 4, NOW + timedelta(minutes=5)
)


def run(coro):
    return asyncio.run(coro)


def make_desired(*, evidence: DependencyEvidence | None = None, image: str = IMAGE) -> DesiredRevision:
    return DesiredRevision(
        resource_key=SCOPE.resource_key,
        revision=1,
        spec={"image_digest": image},
        provenance=Provenance("project", SCOPE.project_id, "revision-1", "correlation-1"),
        evidence=evidence or DependencyEvidence(
            bridge_lease_ref=LEASE.reference,
            bridge_lease_expires_at=LEASE.expires_at,
            bridge_lease_generation=LEASE.generation,
            projected_secret_refs=("vault://runtime/key",),
            image_digest=IMAGE,
            agent_run_id="run-4",
            approval_id="approval-5",
            event_id="event-6",
            contracts_ready=True,
        ),
        operations=(MaterializationOperation("runtime-config", "project/runtime/config", {"enabled": True}),),
        owner_id="project-owner-7",
    )


def make_applied(desired: DesiredRevision, generation: int, *, key: str = "previous-key") -> AppliedRevision:
    manifest = OwnershipManifest.from_desired(desired)
    return AppliedRevision(
        desired.resource_key,
        desired.revision,
        desired.digest,
        generation,
        desired.evidence.image_digest or IMAGE,
        "sha256:" + "c" * 64,
        key,
        desired.provenance,
        desired.spec,
        desired.operations,
        manifest,
    )


class FakeStateStore:
    def __init__(self, generation: int = 3, state=None):
        self.state = state or RuntimeRevisionState(SCOPE.resource_key, generation=generation)
        self.attempts = {}
        self.history = {}

    async def load_state(self, *, resource_key):
        return self.state if resource_key == self.state.resource_key else None

    async def find_attempt(self, *, resource_key, idempotency_key):
        return self.attempts.get((resource_key, idempotency_key))

    async def load_applied_revision(self, *, resource_key, generation):
        return None

    async def claim(self, *, plan, expected_generation):
        return ClaimResult(ClaimStatus.CONFLICT, reason_code="unused_in_prepare_validation")

    async def finish(self, *, claim, state, attempt):
        return False


class FakeOperationValidator:
    async def validate_operation(self, *, resource_key, operation, manifest, prior_manifest, evidence, expected_generation):
        return OperationValidation(operation.operation.operation_id, True)


class FakeRuntime:
    async def apply_operation(self, *, planned, manifest, prior_manifest, evidence, claim):
        return OperationReceipt(planned.operation.operation_id, planned.digest, manifest.owner_id,
                                claim.fence_token, claim.generation, True)

    async def compensate_operation(self, *, planned, prior_manifest, evidence, claim):
        return OperationReceipt(planned.operation.operation_id, planned.digest, prior_manifest.owner_id,
                                claim.fence_token, claim.generation, True)


class FakeAudit:
    async def append(self, *, record):
        return None


class FakeCoreEvidence:
    async def validate_evidence(self, *, resource_key, desired, at):
        return ()


class FakeClock:
    def __init__(self):
        self.current = NOW

    async def now(self):
        return self.current


class FakeRepository:
    def __init__(self, binding: RuntimeApiProjectBinding):
        self.binding = binding
        self.calls = []

    async def load_binding(self, *, scope):
        self.calls.append(scope)
        return self.binding


class FakeEvidence:
    def __init__(self, reasons=()):
        self.reasons = tuple(reasons)
        self.calls = []

    async def validate_binding(self, *, binding, at):
        self.calls.append((binding, at))
        return self.reasons


class FakeExecutor:
    def __init__(self, receipt_transform=None, result_factory=None):
        self.commands = []
        self.receipt_transform = receipt_transform
        self.result_factory = result_factory
        self.completed = {}
        self.repository: Any | None = None
        self.store: Any | None = None
        self.replay_commands = []

    async def replay_atomic(self, *, command):
        self.replay_commands.append(command)
        key = (command.scope.resource_key, command.prepared.plan.idempotency_key)
        store = self.store
        if store is None:
            return None
        attempt = store.attempts.get(key)
        if attempt is not None:
            if attempt.plan_digest != command.prepared.plan.plan_digest:
                result = ApplyResult(
                    ApplyOutcome.CONFLICT, None, store.state, (), "idempotency_key_reused"
                )
            elif attempt.applied_revision is not None:
                result = ApplyResult(
                    ApplyOutcome.REPLAY,
                    attempt.applied_revision,
                    store.state,
                    attempt.diagnostics,
                    "idempotent_replay",
                    True,
                )
            else:
                result = ApplyResult(
                    ApplyOutcome.FAILED,
                    None,
                    store.state,
                    attempt.diagnostics,
                    "replayed_failure",
                    True,
                )
            return RuntimeApiExecutionReceipt.from_state_machine_result(command=command, result=result)
        if command.prepared.plan.action is PlanAction.NOOP and command.prepared.state.applied_revision is not None:
            result = ApplyResult(
                ApplyOutcome.REPLAY,
                command.prepared.state.applied_revision,
                command.prepared.state,
                (),
                "already_materialized",
                True,
            )
            return RuntimeApiExecutionReceipt.from_state_machine_result(command=command, result=result)
        return None

    async def execute_atomic(self, *, command):
        self.commands.append(command)
        key = (command.scope.resource_key, command.prepared.plan.idempotency_key)
        previous = self.completed.get(key)
        if self.result_factory is not None:
            result = self.result_factory(command)
        elif previous is not None:
            prior_result, prior_digest = previous
            if prior_digest != command.prepared.plan.plan_digest:
                result = ApplyResult(ApplyOutcome.CONFLICT, None, prior_result.state, (), "idempotency_key_reused")
            elif prior_result.applied is not None:
                result = ApplyResult(ApplyOutcome.REPLAY, prior_result.applied, prior_result.state,
                                     prior_result.diagnostics, "idempotent_replay", True)
            else:
                result = replace(prior_result, replayed=True)
        elif (self.repository is not None
              and self.repository.binding.generation != command.expected_generation):
            result = ApplyResult(
                ApplyOutcome.CONFLICT,
                None,
                RuntimeRevisionState(command.scope.resource_key, generation=self.repository.binding.generation),
                (),
                "generation_conflict",
            )
        elif command.prepared.plan.action.value == "noop":
            applied = command.prepared.state.applied_revision
            result = ApplyResult(ApplyOutcome.REPLAY, applied, command.prepared.state, (), "already_materialized", True)
        else:
            desired = command.prepared.desired
            applied = AppliedRevision(
                desired.resource_key, desired.revision, desired.digest, command.next_generation,
                desired.evidence.image_digest or IMAGE, command.prepared.plan.plan_digest,
                command.prepared.plan.idempotency_key, desired.provenance, desired.spec,
                desired.operations, OwnershipManifest.from_desired(desired),
            )
            state = RuntimeRevisionState(
                desired.resource_key, command.next_generation, RevisionPhase.APPLIED, applied,
                desired.operations, ownership_manifest=applied.manifest,
            )
            result = ApplyResult(ApplyOutcome.APPLIED, applied, state, (), "applied")
        if not result.replayed and result.outcome in {
            ApplyOutcome.APPLIED, ApplyOutcome.ROLLED_BACK, ApplyOutcome.FAILED,
        }:
            self.completed[key] = (result, command.prepared.plan.plan_digest)
            if self.store is not None and result.state is not None:
                phase = result.state.phase
                self.store.state = result.state
                attempt = AttemptRecord(
                    command.scope.resource_key,
                    command.prepared.plan.idempotency_key,
                    command.prepared.plan.plan_digest,
                    result.state.generation,
                    phase,
                    result.outcome,
                    result.applied,
                    result.diagnostics,
                )
                self.store.attempts[(attempt.resource_key, attempt.idempotency_key)] = attempt
                if result.applied is not None:
                    self.store.history[(result.applied.resource_key, result.applied.generation)] = result.applied
            if self.repository is not None and result.applied is not None:
                self.repository.binding = replace(self.repository.binding, generation=result.applied.generation)
        receipt = RuntimeApiExecutionReceipt.from_state_machine_result(command=command, result=result)
        return self.receipt_transform(receipt) if self.receipt_transform else receipt


class InvalidReportStateMachine:
    def __init__(self, delegate):
        self.delegate = delegate

    async def prepare(self, desired, *, idempotency_key):
        return await self.delegate.prepare(desired, idempotency_key=idempotency_key)

    async def validate(self, prepared, *, at):
        report = await self.delegate.validate(prepared, at=at)
        return replace(report, diagnostics=(RuntimeDiagnostic("validate", "bad_code"), object()))

def make_harness(*, generation=3, executor=True, evidence_reasons=(), prior_state=None):
    binding = RuntimeApiProjectBinding(SCOPE, generation, IMAGE, LEASE)
    repository = FakeRepository(binding)
    clock = FakeClock()
    live_evidence = FakeEvidence(evidence_reasons)
    store = FakeStateStore(generation, prior_state)
    state_machine = RuntimeMaterializationService(
        store=store,
        validator=FakeOperationValidator(),
        runtime=FakeRuntime(),
        audit=FakeAudit(),
        evidence_validator=FakeCoreEvidence(),
        clock=clock,
    )
    atomic_executor = FakeExecutor() if executor else None
    if atomic_executor is not None:
        atomic_executor.repository = repository
        atomic_executor.store = store
    coordinator = RuntimeApiCoordinator(
        repository=repository,
        state_machine=state_machine,
        executor=atomic_executor,
        clock=clock,
        evidence=live_evidence,
    )
    request = RuntimeApiPrepareRequest(SCOPE, generation, IMAGE, LEASE, make_desired(), "idem-7")
    return coordinator, repository, clock, live_evidence, atomic_executor, request


def test_endpoint_catalog_matches_observed_project_pod_routes_and_keeps_mutations_blocked():
    launch = endpoint_contract("post", "/projects/{project_id}/launch")
    sync = endpoint_contract("POST", "/projects/{project_id}/sync")
    reload = endpoint_contract("POST", "/projects/{project_id}/reload")
    lease = endpoint_contract("POST", "/internal/pods/{pod_id}/credentials/{key_id}/lease")
    assert launch is not None and launch.observed_in_api
    assert sync is not None and sync.observed_in_api
    assert reload is not None and reload.observed_in_api
    assert lease is not None and lease.observed_in_api
    assert all(not item.atomic_materialization_supported for item in OBSERVED_RUNTIME_API_ENDPOINTS)
    assert all(not item.observed_in_api for item in UNSUPPORTED_RUNTIME_API_ENDPOINTS)
    assert all(
        RuntimeApiCoordinator.endpoint_gate(method=item.method, path=item.path).outcome is RuntimeApiOutcome.BLOCKED
        for item in OBSERVED_RUNTIME_API_ENDPOINTS + UNSUPPORTED_RUNTIME_API_ENDPOINTS
    )


def test_unsupported_bridge_attestation_and_materialization_routes_are_not_observed():
    assert all(
        endpoint_contract(item.method, item.path) == item
        for item in UNSUPPORTED_RUNTIME_API_ENDPOINTS
    )
    assert all(not item.observed_in_api and not item.atomic_materialization_supported
               for item in UNSUPPORTED_RUNTIME_API_ENDPOINTS)


def test_prepare_creates_validated_generation_bound_plan():
    coordinator, _, _, evidence, _, request = make_harness()

    result = run(coordinator.prepare(request))

    assert result.outcome is RuntimeApiOutcome.PREPARED
    assert result.operation is not None
    assert result.operation.prepared.plan.expected_generation == request.expected_generation
    assert result.operation.prepared.plan.next_generation == request.expected_generation + 1
    assert result.operation.binding.scope == SCOPE
    assert evidence.calls == [(result.operation.binding, NOW)]


def test_state_machine_outcomes_map_one_to_one_and_terminal_results_require_executor_receipt():
    mapping = {
        ApplyOutcome.APPLIED: RuntimeApiOutcome.APPLIED,
        ApplyOutcome.ROLLED_BACK: RuntimeApiOutcome.ROLLED_BACK,
        ApplyOutcome.REPLAY: RuntimeApiOutcome.REPLAYED,
        ApplyOutcome.FAILED: RuntimeApiOutcome.FAILED,
        ApplyOutcome.CONFLICT: RuntimeApiOutcome.CONFLICT,
        ApplyOutcome.BLOCKED: RuntimeApiOutcome.BLOCKED,
    }
    assert {item: RuntimeApiOutcome.from_state_machine(item) for item in ApplyOutcome} == mapping
    with pytest.raises(ValueError, match="atomic executor receipt"):
        from prodavan.application.migration.runtime_api import RuntimeApiResult

        RuntimeApiResult(RuntimeApiOutcome.APPLIED, "applied")


def test_prepare_pins_desired_revision_and_rejects_tampered_plan_revision():
    coordinator, _, _, _, _, request = make_harness()
    prepared = run(coordinator.prepare(request))
    assert prepared.operation is not None
    tampered_plan = replace(prepared.operation.prepared.plan, desired_revision=request.desired.revision + 1)
    tampered_prepared = replace(prepared.operation.prepared, plan=tampered_plan)
    tampered = replace(prepared.operation, prepared=tampered_prepared)

    result = run(coordinator.execute(tampered))

    assert result.outcome is RuntimeApiOutcome.BLOCKED
    assert result.reason_code == "prepared_operation_binding_invalid"


@pytest.mark.parametrize(
    ("binding_scope", "reason"),
    [
        (replace(SCOPE, project_id="other-project"), "exact_project_scope_mismatch"),
        (replace(SCOPE, company_id="other-company"), "exact_project_scope_mismatch"),
        (replace(SCOPE, cabinet_id="other-cabinet"), "exact_project_scope_mismatch"),
        (replace(SCOPE, pod_id="other-pod"), "exact_project_scope_mismatch"),
        (replace(SCOPE, resource_key="other/runtime"), "exact_project_scope_mismatch"),
    ],
)
def test_prepare_requires_exact_project_company_cabinet_pod_scope(binding_scope, reason):
    coordinator, repository, _, _, executor, request = make_harness()
    repository.binding = RuntimeApiProjectBinding(binding_scope, 3, IMAGE, LEASE)

    result = run(coordinator.prepare(request))

    assert result.outcome is RuntimeApiOutcome.BLOCKED
    assert result.reason_code == reason
    assert result.operation is None
    assert executor is None or executor.commands == []


def test_prepare_rejects_stale_generation_before_state_machine_plan():
    coordinator, repository, _, _, _, request = make_harness()
    repository.binding = RuntimeApiProjectBinding(SCOPE, 4, IMAGE, LEASE)

    result = run(coordinator.prepare(request))

    assert result.outcome is RuntimeApiOutcome.CONFLICT
    assert result.reason_code == "generation_conflict"


@pytest.mark.parametrize("binding_image", [None, "latest", "sha256:" + "b" * 64])
def test_prepare_requires_exact_immutable_runtime_image_digest(binding_image):
    coordinator, repository, _, _, _, request = make_harness()
    repository.binding = RuntimeApiProjectBinding(SCOPE, 3, binding_image, LEASE)

    result = run(coordinator.prepare(request))

    assert result.outcome is RuntimeApiOutcome.BLOCKED
    assert result.reason_code == "runtime_image_digest_mismatch"


def test_prepare_rejects_request_image_that_does_not_match_desired_revision():
    coordinator, _, _, _, _, request = make_harness()
    mismatched = replace(request, runtime_image_digest="sha256:" + "b" * 64)

    result = run(coordinator.prepare(mismatched))

    assert result.outcome is RuntimeApiOutcome.BLOCKED
    assert result.reason_code == "runtime_image_digest_mismatch"


@pytest.mark.parametrize(
    "wrong_lease",
    [
        replace(LEASE, reference="bridge-lease://pod-8/other-lease"),
        replace(LEASE, project_id="other-project"),
        replace(LEASE, pod_id="other-pod"),
        replace(LEASE, generation=LEASE.generation + 1),
        replace(LEASE, expires_at=LEASE.expires_at + timedelta(seconds=1)),
    ],
)
def test_prepare_rejects_bridge_lease_reference_generation_and_expiry_mismatch(wrong_lease):
    coordinator, repository, _, _, _, request = make_harness()
    repository.binding = RuntimeApiProjectBinding(SCOPE, 3, IMAGE, wrong_lease)

    result = run(coordinator.prepare(request))

    assert result.outcome is RuntimeApiOutcome.BLOCKED
    assert result.reason_code == "bridge_lease_reference_mismatch"


def test_prepare_rejects_missing_bridge_lease_reference():
    coordinator, _, _, _, _, request = make_harness()
    missing = replace(request, bridge_lease=None)

    result = run(coordinator.prepare(missing))

    assert result.outcome is RuntimeApiOutcome.BLOCKED
    assert result.reason_code == "bridge_lease_reference_missing"


def test_prepare_blocks_expired_revoked_and_untrusted_bridge_lease():
    coordinator, _, clock, _, _, request = make_harness()
    clock.current = LEASE.expires_at
    expired = run(coordinator.prepare(request))
    assert expired.outcome is RuntimeApiOutcome.BLOCKED
    assert expired.reason_code == "bridge_lease_expired"

    coordinator, _, _, _, _, request = make_harness()
    revoked_request = replace(request, desired=make_desired(evidence=replace(request.desired.evidence, revoked=True)))
    revoked = run(coordinator.prepare(revoked_request))
    assert revoked.outcome is RuntimeApiOutcome.BLOCKED
    assert revoked.reason_code == "bridge_lease_revoked"

    coordinator, _, _, _, _, request = make_harness(evidence_reasons=("bridge_lease_subject_mismatch",))
    untrusted = run(coordinator.prepare(request))
    assert untrusted.outcome is RuntimeApiOutcome.BLOCKED
    assert untrusted.reason_code == "trusted_runtime_evidence_rejected"
    assert untrusted.diagnostics[0].reason_code == "bridge_lease_subject_mismatch"


def test_prepare_fails_closed_when_trusted_evidence_port_is_absent():
    coordinator, repository, clock, _, _, request = make_harness()
    coordinator = RuntimeApiCoordinator(
        repository=repository,
        state_machine=RuntimeMaterializationService(
            store=FakeStateStore(), validator=FakeOperationValidator(), runtime=FakeRuntime(), audit=FakeAudit(),
            evidence_validator=FakeCoreEvidence(), clock=clock,
        ),
        executor=None,
        clock=clock,
        evidence=None,
    )

    result = run(coordinator.prepare(request))

    assert result.outcome is RuntimeApiOutcome.BLOCKED
    assert result.reason_code == "trusted_runtime_evidence_port_missing"


def test_prepare_fails_closed_on_malformed_trusted_evidence_diagnostics():
    coordinator, _, _, _, _, request = make_harness(evidence_reasons=(object(),))

    result = run(coordinator.prepare(request))

    assert result.outcome is RuntimeApiOutcome.BLOCKED
    assert result.reason_code == "trusted_runtime_evidence_rejected"
    assert result.diagnostics[0].reason_code == "unspecified_failure"


def test_prepare_fails_closed_on_malformed_state_machine_validation_report():
    coordinator, repository, clock, evidence, executor, request = make_harness()
    coordinator._state_machine = InvalidReportStateMachine(coordinator._state_machine)

    result = run(coordinator.prepare(request))

    assert result.outcome is RuntimeApiOutcome.BLOCKED
    assert result.reason_code == "state_machine_validation_response_invalid"
    assert executor is not None and executor.commands == []


def test_validation_failure_preserves_structured_operation_failure_without_executor_call():
    coordinator, _, _, _, executor, request = make_harness()

    class RejectingStateMachine:
        def __init__(self, delegate):
            self.delegate = delegate

        async def prepare(self, desired, *, idempotency_key):
            return await self.delegate.prepare(desired, idempotency_key=idempotency_key)

        async def validate(self, prepared, *, at):
            report = await self.delegate.validate(prepared, at=at)
            diagnostic = RuntimeDiagnostic("validate", "target_unavailable", "runtime-config", True)
            return replace(report, valid=False, diagnostics=report.diagnostics + (diagnostic,))

    coordinator._state_machine = RejectingStateMachine(coordinator._state_machine)
    rejected = run(coordinator.prepare(request))
    diagnostic = next(item for item in rejected.diagnostics if item.reason_code == "target_unavailable")
    assert diagnostic.operation_id == "runtime-config"
    assert diagnostic.required is True
    assert executor is not None and executor.commands == []


def test_runtime_diagnostic_sanitizes_sensitive_operation_identifiers():
    diagnostic = RuntimeApiDiagnostic("apply", "target_unavailable", "runtime-secret-token", True)
    assert diagnostic.operation_id is None
    assert diagnostic.required is True


def test_execute_rechecks_generation_and_expiry_before_atomic_executor():
    coordinator, repository, clock, _, executor, request = make_harness()
    assert executor is not None
    prepared = run(coordinator.prepare(request))
    assert prepared.operation is not None
    repository.binding = replace(repository.binding, generation=4)
    stale = run(coordinator.execute(prepared.operation))
    assert stale.outcome is RuntimeApiOutcome.CONFLICT
    assert executor.commands == []

    coordinator, _, clock, _, executor, request = make_harness()
    prepared = run(coordinator.prepare(request))
    assert prepared.operation is not None
    clock.current = LEASE.expires_at
    expired = run(coordinator.execute(prepared.operation))
    assert expired.outcome is RuntimeApiOutcome.BLOCKED
    assert expired.reason_code == "bridge_lease_expired"
    assert executor is not None
    assert executor.commands == []


def test_execute_rechecks_live_lease_revocation_before_atomic_executor():
    coordinator, _, _, evidence, executor, request = make_harness()
    assert executor is not None
    prepared = run(coordinator.prepare(request))
    assert prepared.operation is not None
    evidence.reasons = ("bridge_lease_revoked",)

    result = run(coordinator.execute(prepared.operation))

    assert result.outcome is RuntimeApiOutcome.BLOCKED
    assert result.reason_code == "trusted_runtime_evidence_rejected"
    assert executor.commands == []


def test_execute_delegates_only_to_atomic_executor_and_accepts_exact_receipt():
    coordinator, repository, _, evidence, executor, request = make_harness()
    prepared = run(coordinator.prepare(request))
    assert prepared.operation is not None

    result = run(coordinator.execute(prepared.operation))

    assert result.outcome is RuntimeApiOutcome.APPLIED
    assert result.execution is not None and result.execution.generation == 4
    assert executor is not None
    assert len(executor.commands) == 1
    assert executor.commands[0].scope == SCOPE
    assert executor.commands[0].bridge_lease == LEASE
    assert len(repository.calls) == 2
    assert len(evidence.calls) == 2


def test_exact_idempotency_replay_maps_to_replayed_and_keeps_original_generation():
    coordinator, repository, _, _, executor, request = make_harness()
    assert executor is not None
    prepared = run(coordinator.prepare(request))
    assert prepared.operation is not None
    first = run(coordinator.execute(prepared.operation))
    assert first.outcome is RuntimeApiOutcome.APPLIED
    assert repository.binding.generation == request.expected_generation + 1

    replay = run(coordinator.execute(prepared.operation))

    assert replay.outcome is RuntimeApiOutcome.REPLAYED
    assert replay.replayed
    assert replay.execution is not None
    assert first.execution is not None
    assert replay.execution.applied_revision == first.execution.applied_revision
    assert replay.execution.generation == first.execution.generation
    assert len(executor.commands) == 1
    assert len(executor.replay_commands) == 2


def test_noop_plan_returns_replay_at_existing_generation_without_bumping():
    desired = make_desired()
    applied = make_applied(desired, 3)
    prior = RuntimeRevisionState(
        SCOPE.resource_key,
        generation=3,
        phase=RevisionPhase.APPLIED,
        applied_revision=applied,
        owned_operations=desired.operations,
        ownership_manifest=applied.manifest,
    )
    coordinator, repository, _, _, executor, request = make_harness(prior_state=prior)
    assert executor is not None

    prepared = run(coordinator.prepare(request))
    assert prepared.outcome is RuntimeApiOutcome.PREPARED
    assert prepared.operation is not None
    assert prepared.operation.prepared.plan.action is PlanAction.NOOP
    assert prepared.operation.prepared.plan.next_generation == 3
    result = run(coordinator.execute(prepared.operation))

    assert result.outcome is RuntimeApiOutcome.REPLAYED
    assert result.replayed
    assert result.execution is not None and result.execution.generation == 3
    assert repository.binding.generation == 3
    assert executor.commands == []
    assert executor.replay_commands[0].next_generation == 3


def test_retry_plan_after_failed_attempt_advances_generation_and_applies():
    desired = make_desired()
    failed_state = RuntimeRevisionState(
        SCOPE.resource_key,
        generation=3,
        phase=RevisionPhase.FAILED,
        diagnostics=(RuntimeDiagnostic("apply", "target_unavailable", "runtime-config", True),),
    )
    coordinator, repository, _, _, executor, request = make_harness(prior_state=failed_state)
    assert executor is not None

    prepared = run(coordinator.prepare(request))
    assert prepared.operation is not None
    assert prepared.operation.prepared.plan.action is PlanAction.RETRY
    assert prepared.operation.prepared.plan.expected_generation == 3
    assert prepared.operation.prepared.plan.next_generation == 4
    result = run(coordinator.execute(prepared.operation))

    assert result.outcome is RuntimeApiOutcome.APPLIED
    assert result.execution is not None and result.execution.generation == 4
    assert result.execution.applied_revision is not None
    assert result.execution.applied_revision.desired_revision == desired.revision
    assert repository.binding.generation == 4


def test_stale_generation_is_fenced_and_never_returned_as_applied():
    coordinator, repository, _, _, executor, request = make_harness()
    assert executor is not None
    prepared = run(coordinator.prepare(request))
    assert prepared.operation is not None
    repository.binding = replace(repository.binding, generation=request.expected_generation + 1)

    result = run(coordinator.execute(prepared.operation))

    assert result.outcome is RuntimeApiOutcome.CONFLICT
    assert result.reason_code == "generation_conflict"
    assert result.execution is None
    assert executor.commands == []


def test_uncertain_runtime_failure_maps_to_failed_reconciliation_not_applied_or_quarantined():
    coordinator, repository, _, _, existing_executor, request = make_harness()
    assert existing_executor is not None
    uncertain = PlannedOperation(OperationAction.UPSERT, request.desired.operations[0])

    def failed_result(command):
        state = RuntimeRevisionState(
            SCOPE.resource_key,
            generation=command.next_generation,
            phase=RevisionPhase.FAILED,
            uncertain_operations=(uncertain,),
            diagnostics=(RuntimeDiagnostic("apply", "runtime_effect_unknown", "runtime-config", True),),
        )
        return ApplyResult(
            ApplyOutcome.FAILED,
            None,
            state,
            state.diagnostics,
            "required_operation_failed",
        )

    retry_executor = FakeExecutor(result_factory=failed_result)
    retry_executor.repository = repository
    retry_executor.store = existing_executor.store
    coordinator._executor = retry_executor
    prepared = run(coordinator.prepare(request))
    assert prepared.operation is not None
    result = run(coordinator.execute(prepared.operation))

    assert result.outcome is RuntimeApiOutcome.FAILED
    assert result.execution is not None and result.execution.state_machine_outcome is ApplyOutcome.FAILED
    assert result.reconciliation_required
    assert result.execution.applied_revision is None
    assert result.diagnostics[0].operation_id == "runtime-config"
    assert result.diagnostics[0].required is True

    replay = run(coordinator.execute(prepared.operation))
    assert replay.outcome is RuntimeApiOutcome.FAILED
    assert replay.replayed
    assert replay.reconciliation_required
    assert replay.execution is not None and replay.execution.generation == result.execution.generation


def test_quarantine_or_promotion_cannot_be_claimed_by_runtime_materialization_receipt():
    assert "quarantined" not in {item.value for item in RuntimeApiOutcome}
    assert "promoted" not in {item.value for item in RuntimeApiOutcome}
    with pytest.raises(ValueError, match="match state-machine outcome"):
        RuntimeApiExecutionReceipt(
            outcome=RuntimeApiOutcome.APPLIED,
            scope=SCOPE,
            expected_generation=3,
            generation=4,
            plan_digest="sha256:" + "d" * 64,
            runtime_image_digest=IMAGE,
            bridge_lease=LEASE,
            reason_code="applied",
            state_machine_outcome=ApplyOutcome.FAILED,
            replayed=False,
        )


def test_reused_idempotency_key_with_different_plan_digest_conflicts():
    coordinator, _, _, _, executor, request = make_harness()
    assert executor is not None
    prepared = run(coordinator.prepare(request))
    assert prepared.operation is not None
    store = executor.store
    assert store is not None
    store.attempts[(SCOPE.resource_key, request.idempotency_key)] = AttemptRecord(
        SCOPE.resource_key,
        request.idempotency_key,
        "sha256:" + "e" * 64,
        4,
        RevisionPhase.APPLIED,
        ApplyOutcome.APPLIED,
        make_applied(request.desired, 4),
    )

    result = run(coordinator.execute(prepared.operation))

    assert result.outcome is RuntimeApiOutcome.CONFLICT
    assert result.reason_code == "idempotency_key_reused"
    assert executor.commands == []


def test_execute_blocks_when_atomic_executor_missing():
    coordinator, _, _, _, _, request = make_harness(executor=False)
    prepared = run(coordinator.prepare(request))
    assert prepared.operation is not None

    result = run(coordinator.execute(prepared.operation))

    assert result.outcome is RuntimeApiOutcome.BLOCKED
    assert result.reason_code == "atomic_executor_missing"


@pytest.mark.parametrize(
    "receipt_change",
    [
        lambda receipt: replace(receipt, scope=replace(SCOPE, project_id="other-project")),
        lambda receipt: replace(receipt, plan_digest="sha256:" + "b" * 64),
        lambda receipt: replace(receipt, runtime_image_digest="sha256:" + "b" * 64),
        lambda receipt: replace(receipt, bridge_lease=replace(LEASE, generation=LEASE.generation + 1)),
    ],
)
def test_executor_positive_receipt_must_match_scope_plan_image_and_lease(receipt_change):
    coordinator, _, _, _, executor, request = make_harness()
    assert executor is not None
    prepared = run(coordinator.prepare(request))
    assert prepared.operation is not None
    executor.receipt_transform = receipt_change

    result = run(coordinator.execute(prepared.operation))

    assert result.outcome is RuntimeApiOutcome.CONFLICT
    assert result.reason_code == "executor_receipt_binding_mismatch"


def test_executor_positive_receipt_must_match_next_generation():
    coordinator, _, _, _, executor, request = make_harness()
    assert executor is not None
    prepared = run(coordinator.prepare(request))
    assert prepared.operation is not None
    executor.receipt_transform = lambda receipt: replace(receipt, generation=receipt.generation + 1)

    result = run(coordinator.execute(prepared.operation))

    # A positive receipt at any generation other than the fenced next generation
    # is not representable: RuntimeApiExecutionReceipt pins the generation to
    # expected_generation + 1 and to its own applied revision at construction, so
    # the forged object raises inside the executor port before the coordinator
    # can see it. The run therefore ends as a non-authoritative failure rather
    # than a conflict with a receipt reason code.
    assert result.outcome is RuntimeApiOutcome.BLOCKED
    assert result.reason_code == "atomic_executor_failed"
    assert result.execution is None


def test_execution_receipt_type_refuses_a_positive_result_at_another_generation():
    """The next-generation fence is an invariant of the receipt type itself."""
    coordinator, _, _, _, _, request = make_harness()
    prepared = run(coordinator.prepare(request))
    assert prepared.operation is not None
    command_prepared = prepared.operation.prepared
    applied = make_applied(command_prepared.desired, 4)
    receipt = RuntimeApiExecutionReceipt(
        outcome=RuntimeApiOutcome.APPLIED,
        scope=SCOPE,
        expected_generation=3,
        generation=4,
        plan_digest=command_prepared.plan.plan_digest,
        runtime_image_digest=IMAGE,
        bridge_lease=LEASE,
        reason_code="applied",
        state_machine_outcome=ApplyOutcome.APPLIED,
        replayed=False,
        applied_revision=applied,
    )
    with pytest.raises(ValueError, match="must match its applied revision"):
        replace(receipt, generation=5)
    with pytest.raises(ValueError, match="advance exactly one generation"):
        replace(receipt, expected_generation=2)
    with pytest.raises(ValueError, match="must match its applied revision"):
        replace(receipt, applied_revision=make_applied(command_prepared.desired, 5))


def test_execute_rejects_rebound_idempotency_key():
    coordinator, _, _, _, executor, request = make_harness()
    assert executor is not None
    prepared = run(coordinator.prepare(request))
    assert prepared.operation is not None
    tampered = replace(prepared.operation, request=replace(request, idempotency_key="other-key"))

    result = run(coordinator.execute(tampered))

    assert result.outcome is RuntimeApiOutcome.BLOCKED
    assert result.reason_code == "prepared_operation_binding_invalid"
    assert executor is None or executor.commands == []


def test_diagnostics_redact_unstructured_port_exception_details():
    coordinator, repository, _, _, _, request = make_harness()

    async def fail_load_binding(*, scope):
        raise RuntimeError("secret-token-should-not-escape")

    repository.load_binding = fail_load_binding
    result = run(coordinator.prepare(request))

    assert result.outcome is RuntimeApiOutcome.BLOCKED
    assert result.reason_code == "project_binding_read_failed"
    assert "secret-token-should-not-escape" not in repr(result)


def test_bridge_lease_reference_requires_opaque_scoped_typed_values():
    with pytest.raises(ValueError):
        RuntimeApiBridgeLeaseReference("secret plaintext", SCOPE.project_id, SCOPE.pod_id, 4, LEASE.expires_at)
    with pytest.raises(ValueError):
        RuntimeApiBridgeLeaseReference("lease://expired", SCOPE.project_id, SCOPE.pod_id, 0, LEASE.expires_at)
    with pytest.raises(ValueError):
        RuntimeApiBridgeLeaseReference("lease://naive", SCOPE.project_id, SCOPE.pod_id, 4, datetime(2026, 11, 2))


# Tests intentionally authored but not run during migration verification freeze.