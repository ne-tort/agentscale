"""Composition point for a durable resource-materialization apply.

This is the single place that builds the production collaborators, so the caller
path does not construct sessions, heads, attempts, audit or effects by hand:

* ``DurableMaterializationExecutor`` — ``AtomicMaterializationApplyPort`` plus
  attempt history, head CAS, production audit and monotonic rollback;
* ``ResourceMaterializationResolver`` unchanged, driven by the caller's store;
* an optional ``RuntimeMaterializationService`` for the runtime-revision path,
  injected by its owner because its audit port is session-free (see blockers).

The caller path is ``application/resources/api/materialization.py``:
``ResourceMaterializationService(apply_port=...)`` already accepts an
``AtomicMaterializationApplyPort`` and calls it exactly once per apply through
``apply_resolution``. Wiring is therefore one constructor argument::

    application = build_resource_materialization_application(
        session_factory=async_session_factory,
        gate=LinkAndLeaseGate(evidence=..., expected_image_digest=...),
        effects=WorkspaceResourceEffectAdapter(...),
    )
    resource_api_service = ResourceMaterializationService(
        store=store, scopes=scopes, apply_port=application.apply_port
    )

That constructor argument belongs to a module outside this package's file zone,
which is why this module only *prepares* the point: see
``WIRING_BLOCKERS`` for the exact remaining edits and their owners.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.materialization_adapter import ResourceOperation
from prodavan.application.migration.runtime_materialization.state_machine import (
    ApplyOutcome,
    ApplyResult,
    DesiredRevision,
    PreparedRevision,
    ValidationReport,
    canonical_digest,
)
from prodavan.application.resource_materialization.contracts import (
    MaterializationApplyOutcome,
    ResourceMaterializationResolution,
)
from prodavan.application.resource_materialization.durable.audit import (
    RuntimeAuditBinding,
    SqlAlchemyRuntimeAuditWriter,
)
from prodavan.application.resource_materialization.durable.contracts import (
    ApplyPlan,
    AttemptSnapshot,
    DurableApplyResult,
    DurableRollbackCommand,
    RedactedDiagnostic,
    resource_key_for_project,
)
from prodavan.application.resource_materialization.durable.effects import (
    ResourceOperationEffectPort,
    RollbackEffectPort,
)
from prodavan.application.resource_materialization.durable.executor import (
    DurableMaterializationExecutor,
)
from prodavan.application.resource_materialization.durable.ports import (
    DurableApplyGate,
    DurableClockPort,
    SystemUtcClock,
)
from prodavan.application.resource_materialization.service import apply_resolution

WIRING_BLOCKERS: tuple[str, ...] = (
    "resource-api-service-constructor: "
    "application/resources/api/materialization.py must pass apply_port=<executor>; "
    "the module is outside MIG-RES-ATTEMPT-02's file zone",
    "resource-api-transport: the canonical Resource API package has no FastAPI router "
    "or DI registration, so no HTTP caller reaches ResourceMaterializationService yet",
    "runtime-state-machine-audit: RedactedRuntimeAuditPort.append(record) has no session, "
    "so its audit cannot share the executor transaction without changing "
    "application/migration/runtime_materialization/state_machine.py (outside this zone)",
)


class RuntimeMaterializationFacade(Protocol):
    """The subset of ``RuntimeMaterializationService`` this composition calls."""

    async def prepare(self, desired: DesiredRevision, *, idempotency_key: str) -> PreparedRevision: ...

    async def validate(self, prepared: PreparedRevision, *, at: datetime) -> ValidationReport: ...


@dataclass
class ResourceMaterializationApplication:
    """Facade over the durable executor, shared by the resolver path and callers."""

    executor: DurableMaterializationExecutor
    clock: DurableClockPort
    runtime_service: RuntimeMaterializationFacade | None = None

    @property
    def apply_port(self) -> DurableMaterializationExecutor:
        """Pass this into ``ResourceMaterializationService(apply_port=...)``."""

        return self.executor

    async def apply_resolution(
        self, resolution: ResourceMaterializationResolution
    ) -> MaterializationApplyOutcome:
        """Apply a complete resolution exactly once through the fenced CAS."""
        return await apply_resolution(resolution, apply_port=self.executor)

    async def apply_plan(self, plan: ApplyPlan) -> DurableApplyResult:
        return await self.executor.execute(plan)

    async def rollback(self, command: DurableRollbackCommand) -> DurableApplyResult:
        return await self.executor.rollback(command)

    async def history(self, *, project_id: str, limit: int = 50) -> tuple[AttemptSnapshot, ...]:
        return await self.executor.history(project_id=project_id, limit=limit)

    async def generation(self, *, project_id: str) -> int:
        return await self.executor.project_generation(project_id=project_id)

    async def prepare_runtime(
        self, desired: DesiredRevision, *, idempotency_key: str
    ) -> PreparedRevision | None:
        """Delegate runtime-revision prepare to its own service when wired."""
        if self.runtime_service is None:
            return None
        return await self.runtime_service.prepare(desired, idempotency_key=idempotency_key)

    async def validate_runtime(
        self, prepared: PreparedRevision, *, at: datetime
    ) -> ValidationReport | None:
        if self.runtime_service is None:
            return None
        return await self.runtime_service.validate(prepared, at=at)


def build_resource_materialization_application(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    gate: DurableApplyGate,
    effects: ResourceOperationEffectPort | None = None,
    rollback_effects: RollbackEffectPort | None = None,
    clock: DurableClockPort | None = None,
    runtime_service: RuntimeMaterializationFacade | None = None,
) -> ResourceMaterializationApplication:
    """Build the production composition; nothing defaults to an in-memory stub."""
    executor = DurableMaterializationExecutor(
        session_factory=session_factory,
        gate=gate,
        effects=effects,
        rollback_effects=rollback_effects,
        clock=clock,
    )
    return ResourceMaterializationApplication(
        executor=executor,
        clock=clock or SystemUtcClock(),
        runtime_service=runtime_service,
    )


def plan_for_resolution(
    resolution: ResourceMaterializationResolution,
    *,
    correlation_id: str,
    idempotency_key: str,
    revision_id: str | None = None,
    rollback_of_generation: int | None = None,
    created_by: str | None = None,
) -> ApplyPlan:
    """Convert one complete resolution into the durable apply contract.

    ``expected_generation`` is the generation the resolution *advances to*; the
    resolver recorded it as ``context.desired_generation``. An incomplete
    resolution cannot be converted: the caller must refuse it before applying.
    """
    if not resolution.is_complete:
        raise ValueError("an incomplete resource resolution cannot be applied")
    return ApplyPlan(
        project_id=resolution.context.project_id,
        expected_generation=resolution.context.desired_generation,
        snapshot_digest=resolution.snapshot_digest,
        operation_set_digest=resolution.operation_set_digest,
        operations=resolution.operations,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        revision_id=revision_id or resolution.context.revision_id,
        rollback_of_generation=rollback_of_generation,
        created_by=created_by,
    )


def audit_binding_for(
    *,
    resolution: ResourceMaterializationResolution,
    correlation_id: str,
    attempt_id: str | None = None,
    resource_id: str | None = None,
) -> RuntimeAuditBinding:
    return RuntimeAuditBinding(
        project_id=resolution.context.project_id,
        resource_key=resource_key_for_project(resolution.context.project_id),
        correlation_id=correlation_id,
        resource_id=resource_id,
        attempt_id=attempt_id,
    )


def audit_writer_for(binding: RuntimeAuditBinding) -> SqlAlchemyRuntimeAuditWriter:
    """Production writer used by callers that own the transaction (repository path)."""
    return SqlAlchemyRuntimeAuditWriter(binding=binding)


def digest_of_operations(operations: tuple[ResourceOperation, ...]) -> str:
    return canonical_digest(
        [
            {"kind": item.kind.value, "target": item.target, "required": item.required}
            for item in operations
        ]
    )


def blocked_result(reason_code: str) -> DurableApplyResult:
    return DurableApplyResult(
        outcome=MaterializationApplyOutcome.BLOCKED,
        reason_code=reason_code,
        diagnostics=(RedactedDiagnostic("composition", reason_code),),
    )


def apply_result_outcome(result: ApplyResult) -> MaterializationApplyOutcome:
    """Map a state-machine result onto the resolver outcome without inflating it."""
    return {
        ApplyOutcome.APPLIED: MaterializationApplyOutcome.APPLIED,
        ApplyOutcome.ROLLED_BACK: MaterializationApplyOutcome.APPLIED,
        ApplyOutcome.REPLAY: MaterializationApplyOutcome.REPLAY,
        ApplyOutcome.FAILED: MaterializationApplyOutcome.BLOCKED,
        ApplyOutcome.CONFLICT: MaterializationApplyOutcome.CONFLICT,
        ApplyOutcome.BLOCKED: MaterializationApplyOutcome.BLOCKED,
    }[ApplyOutcome(result.outcome)]


__all__ = [
    "WIRING_BLOCKERS",
    "ResourceMaterializationApplication",
    "RuntimeMaterializationFacade",
    "apply_result_outcome",
    "audit_binding_for",
    "audit_writer_for",
    "blocked_result",
    "build_resource_materialization_application",
    "digest_of_operations",
    "plan_for_resolution",
]
