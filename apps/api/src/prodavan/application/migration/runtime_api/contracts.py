"""Typed transport contracts for integrating runtime materialization with API state.

The endpoint catalog records the existing project/Pod surface. None of those
HTTP endpoints is an atomic runtime-materialization executor; integration must
use the abstract repository and fenced executor ports below.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from prodavan.application.migration.runtime_materialization.state_machine import (
    AppliedRevision,
    ApplyOutcome,
    ApplyResult,
    DesiredRevision,
    OwnershipManifest,
    PlanAction,
    PreparedRevision,
    RuntimeDiagnostic,
    ValidationReport,
)

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SAFE_OPERATION_ID = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
_SENSITIVE_OPERATION_MARKERS = ("password", "secret", "token", "credential", "api_key", "private_key")


def _safe_code(value: object, fallback: str) -> str:
    if not isinstance(value, str) or not _CODE.fullmatch(value):
        return fallback
    return fallback if any(marker in value.lower() for marker in _SENSITIVE_OPERATION_MARKERS) else value


class RuntimeApiOutcome(StrEnum):
    PREPARED = "prepared"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    REPLAYED = "replayed"
    FAILED = "failed"
    CONFLICT = "conflict"
    BLOCKED = "blocked"

    @classmethod
    def from_state_machine(cls, outcome: ApplyOutcome) -> "RuntimeApiOutcome":
        """Map the state-machine terminal outcome without collapsing failures or replays."""
        return {
            ApplyOutcome.APPLIED: cls.APPLIED,
            ApplyOutcome.ROLLED_BACK: cls.ROLLED_BACK,
            ApplyOutcome.REPLAY: cls.REPLAYED,
            ApplyOutcome.FAILED: cls.FAILED,
            ApplyOutcome.CONFLICT: cls.CONFLICT,
            ApplyOutcome.BLOCKED: cls.BLOCKED,
        }[ApplyOutcome(outcome)]


class RuntimeApiEndpointKind(StrEnum):
    PROJECT_READ = "project_read"
    PROJECT_CONTAINER_READ = "project_container_read"
    PROJECT_LIFECYCLE = "project_lifecycle"
    POD_WORKSPACE_ARCHIVE = "pod_workspace_archive"
    POD_CREDENTIALS = "pod_credentials"
    POD_CREDENTIAL_LEASE = "pod_credential_lease"
    BRIDGE_LEASE_INSPECTION = "bridge_lease_inspection"
    RUNTIME_IMAGE_ATTESTATION = "runtime_image_attestation"
    MATERIALIZATION_CAS = "materialization_cas"


@dataclass(frozen=True, slots=True)
class RuntimeApiEndpointContract:
    method: str
    path: str
    kind: RuntimeApiEndpointKind
    observed_in_api: bool
    atomic_materialization_supported: bool = False


# Observed routes in api/v1/projects.py and api/internal/pods.py. These routes
# provide project views/lifecycle or credential leases, not a revision CAS.
OBSERVED_RUNTIME_API_ENDPOINTS = (
    RuntimeApiEndpointContract("GET", "/projects/{project_id}", RuntimeApiEndpointKind.PROJECT_READ, True),
    RuntimeApiEndpointContract(
        "GET", "/projects/{project_id}/container", RuntimeApiEndpointKind.PROJECT_CONTAINER_READ, True
    ),
    RuntimeApiEndpointContract("POST", "/projects/{project_id}/launch", RuntimeApiEndpointKind.PROJECT_LIFECYCLE, True),
    RuntimeApiEndpointContract(
        "POST", "/projects/{project_id}/materialize", RuntimeApiEndpointKind.PROJECT_LIFECYCLE, True
    ),
    RuntimeApiEndpointContract("POST", "/projects/{project_id}/sync", RuntimeApiEndpointKind.PROJECT_LIFECYCLE, True),
    RuntimeApiEndpointContract("POST", "/projects/{project_id}/reload", RuntimeApiEndpointKind.PROJECT_LIFECYCLE, True),
    RuntimeApiEndpointContract("POST", "/projects/{project_id}/pause", RuntimeApiEndpointKind.PROJECT_LIFECYCLE, True),
    RuntimeApiEndpointContract("POST", "/projects/{project_id}/resume", RuntimeApiEndpointKind.PROJECT_LIFECYCLE, True),
    RuntimeApiEndpointContract(
        "GET", "/internal/pods/{pod_id}/workspace-archive", RuntimeApiEndpointKind.POD_WORKSPACE_ARCHIVE, True
    ),
    RuntimeApiEndpointContract(
        "GET", "/internal/pods/{pod_id}/credentials", RuntimeApiEndpointKind.POD_CREDENTIALS, True
    ),
    RuntimeApiEndpointContract(
        "POST",
        "/internal/pods/{pod_id}/credentials/{key_id}/lease",
        RuntimeApiEndpointKind.POD_CREDENTIAL_LEASE,
        True,
    ),
    RuntimeApiEndpointContract(
        "DELETE",
        "/internal/pods/{pod_id}/credentials/leases/{lease_id}",
        RuntimeApiEndpointKind.POD_CREDENTIAL_LEASE,
        True,
    ),
)

# These are intentionally contracts, not routes. Do not treat their absence as
# permission to call launch/sync or credential-lease endpoints as substitutes.
UNSUPPORTED_RUNTIME_API_ENDPOINTS = (
    RuntimeApiEndpointContract(
        "GET",
        "/internal/pods/{pod_id}/bridge-leases/{lease_id}",
        RuntimeApiEndpointKind.BRIDGE_LEASE_INSPECTION,
        False,
    ),
    RuntimeApiEndpointContract(
        "POST",
        "/projects/{project_id}/runtime-image/attest",
        RuntimeApiEndpointKind.RUNTIME_IMAGE_ATTESTATION,
        False,
    ),
    RuntimeApiEndpointContract(
        "POST",
        "/projects/{project_id}/runtime-materialization/prepare",
        RuntimeApiEndpointKind.MATERIALIZATION_CAS,
        False,
    ),
    RuntimeApiEndpointContract(
        "POST",
        "/projects/{project_id}/runtime-materialization/apply",
        RuntimeApiEndpointKind.MATERIALIZATION_CAS,
        False,
    ),
)


@dataclass(frozen=True, slots=True)
class RuntimeApiProjectScope:
    project_id: str
    company_id: str
    cabinet_id: str
    pod_id: str
    resource_key: str

    def __post_init__(self) -> None:
        values = (self.project_id, self.company_id, self.cabinet_id, self.pod_id, self.resource_key)
        if any(not isinstance(value, str) or not value.strip() or any(ch.isspace() for ch in value) for value in values):
            raise ValueError("complete, whitespace-free project runtime scope is required")


@dataclass(frozen=True, slots=True)
class RuntimeApiBridgeLeaseReference:
    """Opaque bridge reference bound to the project, Pod and bridge JWT generation."""

    reference: str
    project_id: str
    pod_id: str
    generation: int
    expires_at: datetime

    def __post_init__(self) -> None:
        if (not isinstance(self.reference, str)
                or not self.reference.startswith(("lease://", "bridge-lease://"))
                or any(ch.isspace() for ch in self.reference)
                or not isinstance(self.project_id, str) or not self.project_id.strip()
                or not isinstance(self.pod_id, str) or not self.pod_id.strip()
                or not isinstance(self.generation, int) or isinstance(self.generation, bool) or self.generation < 1):
            raise ValueError("invalid opaque bridge lease reference")
        if (not isinstance(self.expires_at, datetime)
                or self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None):
            raise ValueError("bridge lease expiry must be timezone-aware")


@dataclass(frozen=True, slots=True)
class RuntimeApiProjectBinding:
    """Trusted snapshot resolved from exact scope and the authoritative materialization store.

    ``generation`` is the runtime-materialization CAS generation, not the
    project's resume generation or the Pod's hydrate generation.
    """

    scope: RuntimeApiProjectScope
    generation: int
    runtime_image_digest: str | None
    bridge_lease: RuntimeApiBridgeLeaseReference | None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, RuntimeApiProjectScope):
            raise ValueError("exact project runtime scope is required")
        if (not isinstance(self.generation, int) or isinstance(self.generation, bool) or self.generation < 0):
            raise ValueError("materialization generation cannot be negative")
        if self.runtime_image_digest is not None and not isinstance(self.runtime_image_digest, str):
            raise ValueError("runtime image digest must be text or absent")
        if self.bridge_lease is not None and not isinstance(self.bridge_lease, RuntimeApiBridgeLeaseReference):
            raise ValueError("typed bridge lease reference is required")


@dataclass(frozen=True, slots=True)
class RuntimeApiPrepareRequest:
    scope: RuntimeApiProjectScope
    expected_generation: int
    runtime_image_digest: str
    bridge_lease: RuntimeApiBridgeLeaseReference | None
    desired: DesiredRevision
    idempotency_key: str

    def __post_init__(self) -> None:
        if not isinstance(self.scope, RuntimeApiProjectScope) or not isinstance(self.desired, DesiredRevision):
            raise ValueError("typed project scope and desired runtime revision are required")
        if (not isinstance(self.expected_generation, int) or isinstance(self.expected_generation, bool)
                or self.expected_generation < 0):
            raise ValueError("expected materialization generation must be non-negative")
        if not isinstance(self.runtime_image_digest, str):
            raise ValueError("runtime image digest is required")
        if self.bridge_lease is not None and not isinstance(self.bridge_lease, RuntimeApiBridgeLeaseReference):
            raise ValueError("typed bridge lease reference is required")
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key.strip():
            raise ValueError("idempotency key is required")


@dataclass(frozen=True, slots=True)
class RuntimeApiDiagnostic:
    phase: str
    reason_code: str
    operation_id: str | None = None
    required: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "phase", _safe_code(self.phase, "unknown_phase"))
        object.__setattr__(self, "reason_code", _safe_code(self.reason_code, "unspecified_failure"))
        if self.operation_id is not None and (
            not isinstance(self.operation_id, str)
            or not _SAFE_OPERATION_ID.fullmatch(self.operation_id)
            or any(marker in self.operation_id.lower() for marker in _SENSITIVE_OPERATION_MARKERS)
        ):
            object.__setattr__(self, "operation_id", None)
        if self.required is not None and not isinstance(self.required, bool):
            object.__setattr__(self, "required", None)

    @classmethod
    def from_state_machine(cls, diagnostic: RuntimeDiagnostic) -> "RuntimeApiDiagnostic":
        return cls(diagnostic.phase, diagnostic.reason_code, diagnostic.operation_id, diagnostic.required)


@dataclass(frozen=True, slots=True)
class RuntimeApiPreparedOperation:
    request: RuntimeApiPrepareRequest
    binding: RuntimeApiProjectBinding
    prepared: PreparedRevision
    validation: ValidationReport
    binding_digest: str


@dataclass(frozen=True, slots=True)
class RuntimeApiExecutionCommand:
    scope: RuntimeApiProjectScope
    expected_generation: int
    next_generation: int
    runtime_image_digest: str
    bridge_lease: RuntimeApiBridgeLeaseReference
    prepared: PreparedRevision
    validation: ValidationReport
    binding_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.scope, RuntimeApiProjectScope):
            raise ValueError("execution command requires exact project scope")
        if (not isinstance(self.expected_generation, int) or isinstance(self.expected_generation, bool)
                or self.expected_generation < 0 or not isinstance(self.next_generation, int)
                or isinstance(self.next_generation, bool) or self.next_generation < 0):
            raise ValueError("execution command generations must be non-negative integers")
        if not isinstance(self.runtime_image_digest, str) or not _SHA256.fullmatch(self.runtime_image_digest):
            raise ValueError("execution command requires an immutable image digest")
        if not isinstance(self.bridge_lease, RuntimeApiBridgeLeaseReference):
            raise ValueError("execution command requires a scoped bridge lease")
        if not isinstance(self.prepared, PreparedRevision) or not isinstance(self.validation, ValidationReport):
            raise ValueError("execution command requires typed prepare and validation results")
        if not _SHA256.fullmatch(self.binding_digest):
            raise ValueError("execution command requires a bound project snapshot digest")
        plan, desired = self.prepared.plan, self.prepared.desired
        if (plan.resource_key != self.scope.resource_key or desired.resource_key != self.scope.resource_key
                or plan.expected_generation != self.expected_generation
                or plan.next_generation != self.next_generation
                or plan.action is PlanAction.BLOCKED
                or self.next_generation != self.expected_generation + int(plan.action is not PlanAction.NOOP)
                or plan.desired_digest != desired.digest or plan.desired_revision != desired.revision
                or desired.spec.get("image_digest") != self.runtime_image_digest
                or desired.evidence.image_digest != self.runtime_image_digest
                or desired.evidence.bridge_lease_ref != self.bridge_lease.reference
                or desired.evidence.bridge_lease_generation != self.bridge_lease.generation
                or desired.evidence.bridge_lease_expires_at != self.bridge_lease.expires_at
                or self.bridge_lease.project_id != self.scope.project_id
                or self.bridge_lease.pod_id != self.scope.pod_id
                or not self.validation.valid or self.validation.prepared != self.prepared):
            raise ValueError("execution command scope, desired revision, evidence, or validation mismatch")


@dataclass(frozen=True, slots=True)
class RuntimeApiExecutionReceipt:
    """Executor acknowledgement; positive results are accepted only when fully bound."""

    outcome: RuntimeApiOutcome
    scope: RuntimeApiProjectScope
    expected_generation: int
    generation: int
    plan_digest: str
    runtime_image_digest: str
    bridge_lease: RuntimeApiBridgeLeaseReference
    reason_code: str
    state_machine_outcome: ApplyOutcome
    replayed: bool
    applied_revision: AppliedRevision | None = None
    reconciliation_required: bool = False
    diagnostics: tuple[RuntimeApiDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", RuntimeApiOutcome(self.outcome))
        object.__setattr__(self, "state_machine_outcome", ApplyOutcome(self.state_machine_outcome))
        if self.outcome is not RuntimeApiOutcome.from_state_machine(self.state_machine_outcome):
            raise ValueError("runtime API outcome must match state-machine outcome")
        if not isinstance(self.replayed, bool):
            raise ValueError("replayed flag must be boolean")
        if not isinstance(self.reconciliation_required, bool):
            raise ValueError("reconciliation marker must be boolean")
        if self.outcome is RuntimeApiOutcome.REPLAYED and not self.replayed:
            raise ValueError("replayed outcome must carry its replay marker")
        if self.outcome in {RuntimeApiOutcome.APPLIED, RuntimeApiOutcome.ROLLED_BACK} and self.replayed:
            raise ValueError("newly applied outcomes cannot be marked as replay")
        if not isinstance(self.scope, RuntimeApiProjectScope):
            raise ValueError("execution receipt requires exact project scope")
        if (not isinstance(self.expected_generation, int) or isinstance(self.expected_generation, bool)
                or self.expected_generation < 0 or not isinstance(self.generation, int)
                or isinstance(self.generation, bool) or self.generation < 0):
            raise ValueError("execution receipt generations must be non-negative integers")
        if not _SHA256.fullmatch(self.plan_digest) or not _SHA256.fullmatch(self.runtime_image_digest):
            raise ValueError("execution receipt digests must be immutable sha256 values")
        if not isinstance(self.bridge_lease, RuntimeApiBridgeLeaseReference):
            raise ValueError("execution receipt requires a typed bridge lease")
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, RuntimeApiDiagnostic) for item in self.diagnostics
        ):
            raise ValueError("execution receipt diagnostics must be structured and redacted")
        if self.applied_revision is not None and not isinstance(self.applied_revision, AppliedRevision):
            raise ValueError("execution receipt applied revision must be typed")
        needs_applied = self.outcome in {
            RuntimeApiOutcome.APPLIED, RuntimeApiOutcome.ROLLED_BACK, RuntimeApiOutcome.REPLAYED,
        }
        if needs_applied != (self.applied_revision is not None):
            raise ValueError("positive execution receipt must carry its applied revision")
        if self.applied_revision is not None and self.applied_revision.generation != self.generation:
            raise ValueError("execution receipt generation must match its applied revision")
        if self.outcome in {RuntimeApiOutcome.APPLIED, RuntimeApiOutcome.ROLLED_BACK} and (
            self.generation != self.expected_generation + 1
        ):
            raise ValueError("new apply receipt must advance exactly one generation")
        if self.reconciliation_required and self.outcome in {
            RuntimeApiOutcome.APPLIED, RuntimeApiOutcome.ROLLED_BACK, RuntimeApiOutcome.REPLAYED,
        }:
            raise ValueError("positive runtime outcome cannot require reconciliation")
        object.__setattr__(self, "reason_code", _safe_code(self.reason_code, "executor_rejected"))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    @classmethod
    def from_state_machine_result(
        cls,
        *,
        command: "RuntimeApiExecutionCommand",
        result: ApplyResult,
    ) -> "RuntimeApiExecutionReceipt":
        """Build a bound API receipt using the canonical state-machine outcome map."""
        if not isinstance(command, RuntimeApiExecutionCommand) or not isinstance(result, ApplyResult):
            raise ValueError("typed execution command and state-machine result are required")
        applied = result.applied
        generation = applied.generation if applied is not None else (
            result.state.generation if result.state is not None else command.expected_generation
        )
        if not isinstance(result.diagnostics, tuple) or any(
            not isinstance(item, RuntimeDiagnostic) for item in result.diagnostics
        ):
            raise ValueError("state-machine diagnostics must be structured and typed")
        diagnostics = tuple(RuntimeApiDiagnostic.from_state_machine(item) for item in result.diagnostics)
        return cls(
            outcome=RuntimeApiOutcome.from_state_machine(result.outcome),
            scope=command.scope,
            expected_generation=command.expected_generation,
            generation=generation,
            plan_digest=command.prepared.plan.plan_digest,
            runtime_image_digest=command.runtime_image_digest,
            bridge_lease=command.bridge_lease,
            reason_code=result.reason_code,
            state_machine_outcome=result.outcome,
            replayed=result.replayed,
            applied_revision=applied,
            reconciliation_required=bool(result.state and result.state.uncertain_operations),
            diagnostics=diagnostics,
        )


@dataclass(frozen=True, slots=True)
class RuntimeApiResult:
    outcome: RuntimeApiOutcome
    reason_code: str
    diagnostics: tuple[RuntimeApiDiagnostic, ...] = ()
    operation: RuntimeApiPreparedOperation | None = None
    execution: RuntimeApiExecutionReceipt | None = None
    replayed: bool = False
    reconciliation_required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", RuntimeApiOutcome(self.outcome))
        object.__setattr__(self, "reason_code", _safe_code(self.reason_code, "operation_rejected"))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        if not isinstance(self.replayed, bool):
            raise ValueError("result replay flag must be boolean")
        if not isinstance(self.reconciliation_required, bool):
            raise ValueError("result reconciliation marker must be boolean")
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, RuntimeApiDiagnostic) for item in self.diagnostics
        ):
            raise ValueError("result diagnostics must be structured and redacted")
        if self.execution is not None and not isinstance(self.execution, RuntimeApiExecutionReceipt):
            raise ValueError("execution result must be a typed receipt")
        if self.outcome in {
            RuntimeApiOutcome.APPLIED, RuntimeApiOutcome.ROLLED_BACK,
            RuntimeApiOutcome.REPLAYED, RuntimeApiOutcome.FAILED,
        } and self.execution is None:
            raise ValueError("terminal apply result requires an atomic executor receipt")
        if self.outcome in {
            RuntimeApiOutcome.APPLIED, RuntimeApiOutcome.ROLLED_BACK,
            RuntimeApiOutcome.REPLAYED, RuntimeApiOutcome.FAILED,
        } and (self.execution is None or self.execution.outcome is not self.outcome):
            raise ValueError("terminal result must match its atomic executor receipt")
        if self.outcome is RuntimeApiOutcome.REPLAYED and not self.replayed:
            raise ValueError("replayed result must carry its replay marker")
        if self.execution is not None:
            if self.execution.outcome is not self.outcome:
                raise ValueError("result outcome must match its executor receipt")
            if self.replayed != self.execution.replayed:
                raise ValueError("result replay marker must match its executor receipt")
            if self.reconciliation_required != self.execution.reconciliation_required:
                raise ValueError("result reconciliation marker must match its executor receipt")


class RuntimeApiScopeRepositoryPort(Protocol):
    """Read-only, exact-scope snapshot. Implementations must not infer Pod ownership."""

    async def load_binding(self, *, scope: RuntimeApiProjectScope) -> RuntimeApiProjectBinding | None: ...


class RuntimeApiStateMachinePort(Protocol):
    """Prepare/validate facade over runtime_materialization's abstract ports."""

    async def prepare(self, desired: DesiredRevision, *, idempotency_key: str) -> PreparedRevision: ...

    async def validate(self, prepared: PreparedRevision, *, at: datetime) -> ValidationReport: ...


class RuntimeApiEvidencePort(Protocol):
    """Read-only live proof for image provenance and bridge lease identity/revocation."""

    async def validate_binding(
        self, *, binding: RuntimeApiProjectBinding, at: datetime
    ) -> tuple[str, ...]: ...


class RuntimeApiClockPort(Protocol):
    async def now(self) -> datetime: ...


class RuntimeApiAtomicExecutorPort(Protocol):
    """Adapt the state-machine apply operation over its atomic store/runtime ports.

    The implementation must resolve replay atomically before applying generation
    CAS, and map RuntimeMaterializationService.apply outcomes via
    RuntimeApiExecutionReceipt.from_state_machine_result, using the
    RuntimeRevisionStorePort atomic claim/finish contract and RuntimeOperationPort
    fencing, not a second direct Pod/HTTP path. A positive receipt is legal only
    after runtime effects are fenced and the durable revision/attempt has
    committed. Reject stale scope, plan, image, lease or generation bindings
    without granting authority.
    """

    async def replay_atomic(
        self,
        *,
        command: RuntimeApiExecutionCommand,
    ) -> RuntimeApiExecutionReceipt | None: ...

    async def execute_atomic(self, *, command: RuntimeApiExecutionCommand) -> RuntimeApiExecutionReceipt: ...


def endpoint_contract(method: str, path: str) -> RuntimeApiEndpointContract | None:
    for contract in OBSERVED_RUNTIME_API_ENDPOINTS + UNSUPPORTED_RUNTIME_API_ENDPOINTS:
        if contract.method == method.upper() and contract.path == path:
            return contract
    return None


def is_atomic_materialization_endpoint(method: str, path: str) -> bool:
    contract = endpoint_contract(method, path)
    return bool(contract and contract.observed_in_api and contract.atomic_materialization_supported)


__all__ = [
    "OBSERVED_RUNTIME_API_ENDPOINTS", "UNSUPPORTED_RUNTIME_API_ENDPOINTS", "RuntimeApiAtomicExecutorPort",
    "RuntimeApiBridgeLeaseReference", "RuntimeApiClockPort", "RuntimeApiDiagnostic", "RuntimeApiEndpointContract",
    "RuntimeApiEndpointKind", "RuntimeApiEvidencePort", "RuntimeApiExecutionCommand",
    "RuntimeApiExecutionReceipt", "RuntimeApiOutcome", "RuntimeApiPreparedOperation", "RuntimeApiPrepareRequest",
    "RuntimeApiProjectBinding", "RuntimeApiProjectScope", "RuntimeApiResult", "RuntimeApiScopeRepositoryPort",
    "RuntimeApiStateMachinePort", "endpoint_contract", "is_atomic_materialization_endpoint",
]