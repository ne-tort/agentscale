"""Fail-closed API coordinator for RuntimeMaterialization plans."""
from __future__ import annotations

import re
from datetime import datetime

from prodavan.application.migration.runtime_materialization.state_machine import (
    DesiredRevision,
    MaterializationPlan,
    OwnershipManifest,
    PlanAction,
    PreparedRevision,
    RuntimeDiagnostic,
    RuntimeRevisionState,
    ValidationReport,
)

from .contracts import (
    RuntimeApiAtomicExecutorPort,
    RuntimeApiClockPort,
    RuntimeApiDiagnostic,
    RuntimeApiEvidencePort,
    RuntimeApiExecutionCommand,
    RuntimeApiExecutionReceipt,
    RuntimeApiOutcome,
    RuntimeApiBridgeLeaseReference,
    RuntimeApiPreparedOperation,
    RuntimeApiPrepareRequest,
    RuntimeApiProjectBinding,
    RuntimeApiProjectScope,
    RuntimeApiResult,
    RuntimeApiScopeRepositoryPort,
    RuntimeApiStateMachinePort,
    is_atomic_materialization_endpoint,
)


class RuntimeApiCoordinator:
    """Bind project transport evidence to a state-machine plan and fenced executor.

    No FastAPI route, ORM model, Pod runtime adapter or HTTP client is called
    here. Missing evidence/executor ports and unbacked endpoints deny by default.
    """

    def __init__(
        self,
        *,
        repository: RuntimeApiScopeRepositoryPort,
        state_machine: RuntimeApiStateMachinePort,
        executor: RuntimeApiAtomicExecutorPort | None,
        clock: RuntimeApiClockPort,
        evidence: RuntimeApiEvidencePort | None = None,
    ) -> None:
        self._repository = repository
        self._state_machine = state_machine
        self._executor = executor
        self._clock = clock
        self._evidence = evidence

    @staticmethod
    def endpoint_gate(*, method: str, path: str) -> RuntimeApiResult:
        if is_atomic_materialization_endpoint(method, path):
            return RuntimeApiResult(RuntimeApiOutcome.PREPARED, "endpoint_contract_available")
        return RuntimeApiResult(
            RuntimeApiOutcome.BLOCKED,
            "endpoint_not_supported_for_atomic_materialization",
            (RuntimeApiDiagnostic("transport", "endpoint_not_supported_for_atomic_materialization"),),
        )

    async def prepare(self, request: RuntimeApiPrepareRequest) -> RuntimeApiResult:
        if not isinstance(request, RuntimeApiPrepareRequest):
            return self._blocked("request", "prepare_request_invalid")
        if not isinstance(request.scope, RuntimeApiProjectScope) or not isinstance(request.desired, DesiredRevision):
            return self._blocked("request", "prepare_request_invalid")
        if (not isinstance(request.expected_generation, int) or isinstance(request.expected_generation, bool)
                or request.expected_generation < 0):
            return self._blocked("request", "invalid_expected_generation")
        if not isinstance(request.idempotency_key, str) or not request.idempotency_key.strip():
            return self._blocked("request", "idempotency_key_missing")
        if request.bridge_lease is not None and not isinstance(request.bridge_lease, RuntimeApiBridgeLeaseReference):
            return self._blocked("bridge", "bridge_lease_reference_invalid")
        if request.desired.resource_key != request.scope.resource_key:
            return self._blocked("scope", "desired_resource_scope_mismatch")

        try:
            binding = await self._repository.load_binding(scope=request.scope)
        except Exception:
            return self._blocked("repository", "project_binding_read_failed")
        if binding is None:
            return self._blocked("repository", "project_binding_missing")
        if not isinstance(binding, RuntimeApiProjectBinding):
            return self._blocked("repository", "project_binding_invalid")

        now = await self._now()
        if isinstance(now, RuntimeApiResult):
            return now
        binding_error = self._binding_error(request, binding, at=now)
        if binding_error is not None:
            return binding_error
        evidence_error = await self._validate_evidence(binding, at=now)
        if evidence_error is not None:
            return evidence_error

        try:
            prepared = await self._state_machine.prepare(
                request.desired, idempotency_key=request.idempotency_key
            )
        except Exception:
            return self._blocked("prepare", "state_machine_prepare_failed")
        if (not isinstance(prepared, PreparedRevision)
                or not isinstance(prepared.plan, MaterializationPlan)
                or not isinstance(prepared.state, RuntimeRevisionState)):
            return self._blocked("prepare", "state_machine_prepare_response_invalid")
        plan = prepared.plan
        if (prepared.desired.digest != request.desired.digest or prepared.state.resource_key != request.scope.resource_key
                or prepared.state.generation != binding.generation or plan.resource_key != request.scope.resource_key
                or plan.expected_generation != request.expected_generation
                or plan.desired_digest != request.desired.digest
                or plan.desired_revision != request.desired.revision
                or plan.idempotency_key != request.idempotency_key
                or plan.next_generation != request.expected_generation + int(plan.action is not PlanAction.NOOP)):
            return self._conflict("prepare", "prepared_plan_binding_mismatch")

        try:
            report = await self._state_machine.validate(prepared, at=now)
        except Exception:
            return self._blocked("validate", "state_machine_validation_failed")
        if not self._valid_report(report, prepared):
            return self._blocked("validate", "state_machine_validation_response_invalid")
        operation = RuntimeApiPreparedOperation(
            request=request,
            binding=binding,
            prepared=prepared,
            validation=report,
            binding_digest=self._binding_digest(binding),
        )
        if plan.action is PlanAction.BLOCKED or not report.valid:
            diagnostics = self._core_diagnostics(report.diagnostics)
            if not diagnostics:
                diagnostics = (RuntimeApiDiagnostic("validate", "state_machine_plan_blocked"),)
            return RuntimeApiResult(RuntimeApiOutcome.BLOCKED, "runtime_plan_blocked", diagnostics, operation)
        return RuntimeApiResult(RuntimeApiOutcome.PREPARED, "runtime_plan_prepared", (), operation)

    async def execute(self, operation: RuntimeApiPreparedOperation) -> RuntimeApiResult:
        if not isinstance(operation, RuntimeApiPreparedOperation):
            return self._blocked("execute", "prepared_operation_invalid")
        if (not isinstance(operation.request, RuntimeApiPrepareRequest)
                or not isinstance(operation.binding, RuntimeApiProjectBinding)
                or not isinstance(operation.prepared, PreparedRevision)
                or not isinstance(operation.prepared.plan, MaterializationPlan)
                or not isinstance(operation.validation, ValidationReport)
                or not isinstance(operation.validation.prepared, PreparedRevision)):
            return self._blocked("execute", "prepared_operation_binding_invalid")
        request = operation.request
        plan = operation.prepared.plan
        if (operation.binding_digest != self._binding_digest(operation.binding)
                or operation.prepared.desired.digest != request.desired.digest
                or plan.desired_digest != request.desired.digest
                or plan.desired_revision != request.desired.revision
                or plan.idempotency_key != request.idempotency_key
                or plan.expected_generation != request.expected_generation
                or plan.next_generation != request.expected_generation + int(
                    plan.action not in {PlanAction.NOOP, PlanAction.BLOCKED}
                )
                or plan.plan_digest != operation.validation.prepared.plan.plan_digest
                or operation.validation.prepared != operation.prepared
                or not operation.validation.valid or plan.action is PlanAction.BLOCKED):
            return self._blocked("execute", "prepared_operation_binding_invalid", operation=operation)
        if operation.binding.scope != request.scope or plan.resource_key != request.scope.resource_key:
            return self._blocked("scope", "prepared_project_scope_mismatch", operation=operation)
        if request.bridge_lease is None:
            return self._blocked("bridge", "bridge_lease_reference_missing", operation=operation)
        if not isinstance(request.bridge_lease, RuntimeApiBridgeLeaseReference):
            return self._blocked("bridge", "bridge_lease_reference_invalid", operation=operation)
        if self._executor is None:
            return self._blocked("execute", "atomic_executor_missing", operation=operation)

        try:
            current = await self._repository.load_binding(scope=request.scope)
        except Exception:
            return self._blocked("repository", "project_binding_read_failed", operation=operation)
        if current is None:
            return self._blocked("repository", "project_binding_missing", operation=operation)
        if not isinstance(current, RuntimeApiProjectBinding):
            return self._blocked("repository", "project_binding_invalid", operation=operation)
        now = await self._now(operation=operation)
        if isinstance(now, RuntimeApiResult):
            return now
        binding_error = self._binding_error(
            request, current, at=now, operation=operation, check_generation=False
        )
        if binding_error is not None:
            return binding_error
        if not self._same_runtime_binding(current, operation.binding):
            return self._conflict("binding", "project_runtime_binding_changed", operation=operation)
        evidence_error = await self._validate_evidence(current, at=now, operation=operation)
        if evidence_error is not None:
            return evidence_error

        if not self._valid_report(operation.validation, operation.prepared) or not operation.validation.valid:
            return self._blocked("replay", "prepared_validation_binding_invalid", operation=operation)
        replay_command = RuntimeApiExecutionCommand(
            scope=request.scope,
            expected_generation=plan.expected_generation,
            next_generation=plan.next_generation,
            runtime_image_digest=request.runtime_image_digest,
            bridge_lease=request.bridge_lease,
            prepared=operation.prepared,
            validation=operation.validation,
            binding_digest=operation.binding_digest,
        )
        try:
            replay_receipt = await self._executor.replay_atomic(command=replay_command)
        except Exception:
            return self._blocked("replay", "atomic_replay_failed", operation=operation)
        if replay_receipt is not None:
            if not isinstance(replay_receipt, RuntimeApiExecutionReceipt):
                return self._blocked("replay", "atomic_replay_receipt_invalid", operation=operation)
            receipt = replay_receipt
            receipt_error = self._receipt_error(receipt, replay_command)
            if receipt_error is not None:
                return self._conflict("replay", receipt_error, operation=operation)
            if receipt.outcome is RuntimeApiOutcome.CONFLICT:
                return self._conflict("replay", receipt.reason_code, operation=operation)
            if receipt.outcome not in {RuntimeApiOutcome.REPLAYED, RuntimeApiOutcome.FAILED} or not receipt.replayed:
                return self._blocked("replay", "atomic_replay_outcome_invalid", operation=operation)
            return RuntimeApiResult(
                receipt.outcome,
                receipt.reason_code,
                receipt.diagnostics,
                operation,
                receipt,
                receipt.replayed,
                receipt.reconciliation_required,
            )

        if current.generation != plan.expected_generation:
            return self._conflict("generation", "generation_conflict", operation=operation)
        try:
            report = await self._state_machine.validate(operation.prepared, at=now)
        except Exception:
            return self._blocked("validate", "state_machine_validation_failed", operation=operation)
        if not self._valid_report(report, operation.prepared):
            return self._blocked("validate", "state_machine_validation_response_invalid", operation=operation)
        if not report.valid:
            diagnostics = self._core_diagnostics(report.diagnostics)
            return RuntimeApiResult(
                RuntimeApiOutcome.BLOCKED,
                "runtime_evidence_or_operation_validation_failed",
                diagnostics or (RuntimeApiDiagnostic("validate", "runtime_validation_failed"),),
                operation,
            )
        command = RuntimeApiExecutionCommand(
            scope=request.scope,
            expected_generation=plan.expected_generation,
            next_generation=plan.next_generation,
            runtime_image_digest=request.runtime_image_digest,
            bridge_lease=request.bridge_lease,
            prepared=operation.prepared,
            validation=report,
            binding_digest=operation.binding_digest,
        )
        try:
            receipt = await self._executor.execute_atomic(command=command)
        except Exception:
            return self._blocked("execute", "atomic_executor_failed", operation=operation)
        if not isinstance(receipt, RuntimeApiExecutionReceipt):
            return self._blocked("execute", "executor_receipt_invalid", operation=operation)
        receipt_error = self._receipt_error(receipt, command)
        if receipt_error is not None:
            return self._conflict("execute", receipt_error, operation=operation)
        return RuntimeApiResult(
            receipt.outcome,
            receipt.reason_code,
            receipt.diagnostics,
            operation,
            receipt,
            receipt.replayed,
            receipt.reconciliation_required,
        )

    async def _validate_evidence(
        self,
        binding: RuntimeApiProjectBinding,
        *,
        at: datetime,
        operation: RuntimeApiPreparedOperation | None = None,
    ) -> RuntimeApiResult | None:
        if self._evidence is None:
            return self._blocked("evidence", "trusted_runtime_evidence_port_missing", operation=operation)
        try:
            reasons = await self._evidence.validate_binding(binding=binding, at=at)
        except Exception:
            return self._blocked("evidence", "trusted_runtime_evidence_port_failed", operation=operation)
        if not isinstance(reasons, (tuple, list)):
            return self._blocked("evidence", "trusted_runtime_evidence_response_invalid", operation=operation)
        try:
            diagnostics = tuple(RuntimeApiDiagnostic("evidence", reason) for reason in reasons)
        except Exception:
            return self._blocked("evidence", "trusted_runtime_evidence_response_invalid", operation=operation)
        if diagnostics:
            return RuntimeApiResult(RuntimeApiOutcome.BLOCKED, "trusted_runtime_evidence_rejected", diagnostics, operation)
        return None

    def _binding_error(
        self,
        request: RuntimeApiPrepareRequest,
        binding: RuntimeApiProjectBinding,
        *,
        at: datetime,
        operation: RuntimeApiPreparedOperation | None = None,
        check_generation: bool = True,
    ) -> RuntimeApiResult | None:
        if binding.scope != request.scope:
            return self._blocked("scope", "exact_project_scope_mismatch", operation=operation)
        if check_generation and binding.generation != request.expected_generation:
            return self._conflict("generation", "generation_conflict", operation=operation)
        digest = request.runtime_image_digest
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            return self._blocked("image", "runtime_image_digest_invalid", operation=operation)
        desired_digest = request.desired.spec.get("image_digest")
        if binding.runtime_image_digest != digest or desired_digest != digest or request.desired.evidence.image_digest != digest:
            return self._blocked("image", "runtime_image_digest_mismatch", operation=operation)
        lease = request.bridge_lease
        actual_lease = binding.bridge_lease
        evidence = request.desired.evidence
        if lease is None or actual_lease is None:
            return self._blocked("bridge", "bridge_lease_reference_missing", operation=operation)
        if (lease != actual_lease or lease.project_id != request.scope.project_id
                or lease.pod_id != request.scope.pod_id
                or evidence.bridge_lease_ref != lease.reference
                or evidence.bridge_lease_generation != lease.generation
                or evidence.bridge_lease_expires_at != lease.expires_at):
            return self._blocked("bridge", "bridge_lease_reference_mismatch", operation=operation)
        if evidence.revoked:
            return self._blocked("bridge", "bridge_lease_revoked", operation=operation)
        if lease.expires_at <= at:
            return self._blocked("bridge", "bridge_lease_expired", operation=operation)
        return None

    @staticmethod
    def _same_runtime_binding(
        left: RuntimeApiProjectBinding,
        right: RuntimeApiProjectBinding,
    ) -> bool:
        """Compare trusted project evidence while allowing generation-only advancement."""
        return (
            left.scope == right.scope
            and left.runtime_image_digest == right.runtime_image_digest
            and left.bridge_lease == right.bridge_lease
        )

    @staticmethod
    def _receipt_error(
        receipt: RuntimeApiExecutionReceipt,
        command: RuntimeApiExecutionCommand,
    ) -> str | None:
        if not isinstance(receipt, RuntimeApiExecutionReceipt):
            return "executor_receipt_invalid"
        if not isinstance(receipt.diagnostics, tuple) or any(
            not isinstance(item, RuntimeApiDiagnostic) for item in receipt.diagnostics
        ):
            return "executor_receipt_diagnostics_invalid"
        plan = command.prepared.plan
        if (receipt.scope != command.scope or receipt.expected_generation != command.expected_generation
                or receipt.plan_digest != plan.plan_digest
                or receipt.runtime_image_digest != command.runtime_image_digest
                or receipt.bridge_lease != command.bridge_lease):
            return "executor_receipt_binding_mismatch"
        applied = receipt.applied_revision
        if applied is not None and (
            applied.resource_key != command.scope.resource_key
            or applied.desired_revision != command.prepared.desired.revision
            or applied.desired_digest != command.prepared.desired.digest
            or applied.image_digest != command.runtime_image_digest
            or applied.provenance != command.prepared.desired.provenance
            or applied.manifest.owner_id != plan.desired_manifest.owner_id
            or (
                receipt.outcome is RuntimeApiOutcome.REPLAYED
                and plan.action is not PlanAction.NOOP
                and applied.idempotency_key != plan.idempotency_key
            )
            or (
                receipt.outcome is not RuntimeApiOutcome.REPLAYED
                and applied.idempotency_key != plan.idempotency_key
            )
            or (
                receipt.outcome is not RuntimeApiOutcome.REPLAYED
                and applied.plan_digest != plan.plan_digest
            )
            or (
                receipt.outcome is RuntimeApiOutcome.REPLAYED
                and plan.action is not PlanAction.NOOP
                and applied.plan_digest != plan.plan_digest
            )
        ):
            return "executor_receipt_revision_mismatch"
        if applied is not None:
            actual_manifest = OwnershipManifest.from_operations(
                applied.resource_key, applied.manifest.owner_id, applied.operations
            )
            desired_entries = {
                (entry.operation_id, entry.target_ref): entry
                for entry in plan.desired_manifest.entries
            }
            expected_keys = {
                key for key in desired_entries
                if key[0] not in command.validation.skipped_optional
            }
            actual_keys = {(op.operation_id, op.target_ref) for op in applied.operations}
            if actual_manifest.digest != applied.manifest.digest or any(
                (entry := desired_entries.get((op.operation_id, op.target_ref))) is None
                or entry.operation_digest != op.digest
                or entry.required != op.required
                for op in applied.operations
            ) or actual_keys != expected_keys:
                return "executor_receipt_ownership_mismatch"
        if receipt.outcome in {RuntimeApiOutcome.APPLIED, RuntimeApiOutcome.ROLLED_BACK}:
            if applied is None or receipt.generation != command.next_generation or applied.generation != receipt.generation:
                return "executor_receipt_generation_mismatch"
        elif receipt.outcome is RuntimeApiOutcome.REPLAYED:
            if applied is None or receipt.generation != applied.generation:
                return "executor_receipt_generation_mismatch"
        elif receipt.outcome is RuntimeApiOutcome.CONFLICT:
            if receipt.generation < command.expected_generation:
                return "executor_receipt_generation_mismatch"
        elif receipt.generation not in {command.expected_generation, command.next_generation}:
            return "executor_receipt_generation_mismatch"
        if receipt.outcome is RuntimeApiOutcome.PREPARED:
            return "executor_receipt_outcome_invalid"
        if plan.action is PlanAction.NOOP and receipt.outcome not in {
            RuntimeApiOutcome.REPLAYED,
            RuntimeApiOutcome.BLOCKED,
            RuntimeApiOutcome.CONFLICT,
        }:
            return "executor_noop_outcome_invalid"
        if plan.action is PlanAction.ROLLBACK and receipt.outcome is RuntimeApiOutcome.APPLIED:
            return "executor_rollback_outcome_invalid"
        if plan.action is not PlanAction.ROLLBACK and receipt.outcome is RuntimeApiOutcome.ROLLED_BACK:
            return "executor_rollback_outcome_invalid"
        if receipt.replayed and receipt.outcome not in {RuntimeApiOutcome.REPLAYED, RuntimeApiOutcome.FAILED}:
            return "executor_replay_marker_invalid"
        return None

    async def _now(
        self,
        *,
        operation: RuntimeApiPreparedOperation | None = None,
    ) -> datetime | RuntimeApiResult:
        try:
            now = await self._clock.now()
        except Exception:
            return self._blocked("clock", "trusted_clock_failed", operation=operation)
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            return self._blocked("clock", "trusted_clock_invalid", operation=operation)
        return now

    @staticmethod
    def _binding_digest(binding: RuntimeApiProjectBinding) -> str:
        from prodavan.application.migration.runtime_materialization.state_machine import canonical_digest

        lease = binding.bridge_lease
        return canonical_digest({
            "scope": {
                "project_id": binding.scope.project_id,
                "company_id": binding.scope.company_id,
                "cabinet_id": binding.scope.cabinet_id,
                "pod_id": binding.scope.pod_id,
                "resource_key": binding.scope.resource_key,
            },
            "generation": binding.generation,
            "runtime_image_digest": binding.runtime_image_digest,
            "bridge_lease": None if lease is None else {
                "reference": lease.reference,
                "project_id": lease.project_id,
                "pod_id": lease.pod_id,
                "generation": lease.generation,
                "expires_at": lease.expires_at,
            },
        })

    @staticmethod
    def _core_diagnostics(diagnostics: tuple[RuntimeDiagnostic, ...]) -> tuple[RuntimeApiDiagnostic, ...]:
        return tuple(
            RuntimeApiDiagnostic.from_state_machine(item)
            for item in diagnostics
        )

    @staticmethod
    def _valid_report(report: object, prepared: PreparedRevision) -> bool:
        return (
            isinstance(report, ValidationReport)
            and report.prepared == prepared
            and isinstance(report.valid, bool)
            and isinstance(report.diagnostics, tuple)
            and all(isinstance(item, RuntimeDiagnostic) for item in report.diagnostics)
            and isinstance(report.skipped_optional, tuple)
            and all(isinstance(item, str) for item in report.skipped_optional)
            and len(set(report.skipped_optional)) == len(report.skipped_optional)
            and set(report.skipped_optional).issubset({
                planned.operation.operation_id
                for planned in prepared.plan.operations
                if not planned.required
            })
            and isinstance(report.checked_at, datetime)
            and report.checked_at.tzinfo is not None
            and report.checked_at.utcoffset() is not None
        )

    @staticmethod
    def _blocked(
        phase: str,
        code: str,
        *,
        operation: RuntimeApiPreparedOperation | None = None,
    ) -> RuntimeApiResult:
        diagnostic = RuntimeApiDiagnostic(phase, code)
        return RuntimeApiResult(RuntimeApiOutcome.BLOCKED, diagnostic.reason_code, (diagnostic,), operation)

    @staticmethod
    def _conflict(
        phase: str,
        code: str,
        *,
        operation: RuntimeApiPreparedOperation | None = None,
    ) -> RuntimeApiResult:
        diagnostic = RuntimeApiDiagnostic(phase, code)
        return RuntimeApiResult(RuntimeApiOutcome.CONFLICT, diagnostic.reason_code, (diagnostic,), operation)


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")

__all__ = ["RuntimeApiCoordinator"]