"""MIG-RES-02 runtime revision state machine.

This is a port-only application service. Durable state/CAS, operation validation,
Pod or bridge effects, and redacted audit are supplied by adapters; no live-system
adapter, DDL, or cutover is included.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping, Protocol

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SECRET_MARKERS = ("password", "secret", "token", "api_key", "credential", "private_key")


def _canonical_value(value: object) -> object:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, MappingABC):
        return {str(k): _canonical_value(v) for k, v in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (tuple, list)):
        return [_canonical_value(v) for v in value]
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("canonical timestamps must be timezone-aware")
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_digest(value: object) -> str:
    raw = json.dumps(_canonical_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _freeze(value: object) -> object:
    if isinstance(value, MappingABC):
        return MappingProxyType({str(k): _freeze(v) for k, v in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(v) for v in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported immutable payload type: {type(value).__name__}")


def _is_secret_ref(value: object) -> bool:
    """Only external reference forms are accepted; secret material is never resolved here."""
    if not isinstance(value, str) or not value.strip() or any(ch.isspace() for ch in value):
        return False
    lowered = value.lower()
    return lowered.startswith(("vault://", "secret://", "file://", "${")) and not lowered.startswith(
        ("password=", "secret=", "token=", "api_key=")
    )


def _reject_secret_values(value: object) -> None:
    if isinstance(value, MappingABC):
        for key, item in value.items():
            name = str(key).lower()
            marked = any(marker in name for marker in _SECRET_MARKERS)
            reference = marked and name.endswith(("_ref", "_reference"))
            if marked and (not reference or not _is_secret_ref(item)):
                raise ValueError("secret values are forbidden; use an external secret reference")
            if isinstance(item, str) and item.lower().startswith(("password=", "secret=", "token=", "api_key=")):
                raise ValueError("inline secret material is forbidden")
            _reject_secret_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_secret_values(item)


def _secret_refs(value: object) -> tuple[str, ...]:
    refs: set[str] = set()
    if isinstance(value, MappingABC):
        for key, item in value.items():
            name = str(key).lower()
            if any(marker in name for marker in _SECRET_MARKERS) and name.endswith(("_ref", "_reference")) and _is_secret_ref(item):
                refs.add(str(item))
            refs.update(_secret_refs(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            refs.update(_secret_refs(item))
    return tuple(sorted(refs))


def _safe_code(value: object, fallback: str = "operation_rejected") -> str:
    return value if isinstance(value, str) and _CODE.fullmatch(value) else fallback


class PlanAction(StrEnum):
    CREATE = "create"
    NOOP = "noop"
    REPLACE = "replace"
    RETRY = "retry"
    ROLLBACK = "rollback"
    BLOCKED = "blocked"


class RevisionPhase(StrEnum):
    IDLE = "idle"
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class OperationAction(StrEnum):
    UPSERT = "upsert"
    REMOVE = "remove"


class ApplyOutcome(StrEnum):
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    REPLAY = "replay"
    FAILED = "failed"
    CONFLICT = "conflict"
    BLOCKED = "blocked"


class ClaimStatus(StrEnum):
    CLAIMED = "claimed"
    REPLAY = "replay"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class Provenance:
    source_kind: str
    source_id: str
    source_revision: str
    correlation_id: str

    def __post_init__(self) -> None:
        if not all(isinstance(v, str) and v.strip() for v in (
            self.source_kind, self.source_id, self.source_revision, self.correlation_id
        )):
            raise ValueError("complete source provenance and correlation are required")

    def canonical(self) -> dict[str, str]:
        return {"source_kind": self.source_kind, "source_id": self.source_id,
                "source_revision": self.source_revision, "correlation_id": self.correlation_id}


@dataclass(frozen=True, slots=True)
class MaterializationOperation:
    operation_id: str
    target_ref: str
    payload: Mapping[str, object]
    required: bool = True

    def __post_init__(self) -> None:
        if not self.operation_id.strip() or not self.target_ref.strip():
            raise ValueError("operation id and target reference are required")
        payload = _freeze(self.payload)
        _reject_secret_values(payload)
        object.__setattr__(self, "payload", payload)

    def canonical(self) -> dict[str, object]:
        return {"operation_id": self.operation_id, "target_ref": self.target_ref,
                "payload": self.payload, "required": self.required}

    @property
    def digest(self) -> str:
        return canonical_digest(self.canonical())


@dataclass(frozen=True, slots=True)
class DependencyEvidence:
    bridge_lease_ref: str | None = None
    projected_secret_ref: str | None = None
    image_digest: str | None = None
    agent_run_id: str | None = None
    approval_id: str | None = None
    event_id: str | None = None
    contracts_ready: bool = False
    bridge_lease_expires_at: datetime | None = None
    bridge_lease_generation: int = 0
    projected_secret_refs: tuple[str, ...] = ()
    revoked: bool = False

    def __post_init__(self) -> None:
        if self.bridge_lease_ref and not self.bridge_lease_ref.startswith(("lease://", "bridge-lease://")):
            raise ValueError("bridge lease must be an opaque external reference")
        if self.image_digest is not None and not self.image_digest.strip():
            raise ValueError("image evidence cannot be blank")
        refs = set(self.projected_secret_refs)
        if self.projected_secret_ref:
            refs.add(self.projected_secret_ref)
        if any(not _is_secret_ref(ref) for ref in refs):
            raise ValueError("projected secret evidence must contain opaque external references")
        object.__setattr__(self, "projected_secret_refs", tuple(sorted(refs)))
        if self.bridge_lease_expires_at is not None and (
            self.bridge_lease_expires_at.tzinfo is None or self.bridge_lease_expires_at.utcoffset() is None
        ):
            raise ValueError("bridge lease expiry must be timezone-aware")
        if self.bridge_lease_generation < 0:
            raise ValueError("bridge lease generation cannot be negative")

    def static_blockers(self, desired: "DesiredRevision") -> tuple[str, ...]:
        """Evidence checks independent of the current clock."""
        out: list[str] = []
        if not self.contracts_ready:
            out.append("upstream_resource_contract_missing")
        if not self.bridge_lease_ref:
            out.append("bridge_lease_missing")
        if self.bridge_lease_generation < 1:
            out.append("bridge_lease_generation_missing")
        if self.bridge_lease_expires_at is None:
            out.append("bridge_lease_expiry_missing")
        if self.revoked:
            out.append("bridge_lease_revoked")
        if not self.image_digest:
            out.append("immutable_image_digest_missing")
        elif not _SHA256.fullmatch(self.image_digest):
            out.append("immutable_image_digest_invalid")
        image = desired.spec.get("image_digest")
        if not isinstance(image, str) or not _SHA256.fullmatch(image):
            out.append("desired_immutable_image_digest_missing")
        elif self.image_digest != image:
            out.append("image_digest_evidence_mismatch")
        required_refs = set(_secret_refs(desired.spec))
        for op in desired.operations:
            required_refs.update(_secret_refs(op.payload))
        if not self.projected_secret_refs or not required_refs.issubset(self.projected_secret_refs):
            out.append("projected_secret_reference_missing")
        if not self.agent_run_id:
            out.append("agent_run_link_missing")
        if not self.approval_id:
            out.append("approval_link_missing")
        if not self.event_id:
            out.append("event_link_missing")
        return tuple(dict.fromkeys(out))

    def blockers(self, desired: "DesiredRevision", *, at: datetime) -> tuple[str, ...]:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("validation time must be timezone-aware")
        out = list(self.static_blockers(desired))
        if self.bridge_lease_expires_at is not None and self.bridge_lease_expires_at <= at:
            out.append("bridge_lease_expired")
        return tuple(dict.fromkeys(out))

    def canonical(self) -> dict[str, object]:
        return {"bridge_lease_ref": self.bridge_lease_ref,
                "bridge_lease_expires_at": self.bridge_lease_expires_at,
                "bridge_lease_generation": self.bridge_lease_generation,
                "projected_secret_refs": self.projected_secret_refs,
                "image_digest": self.image_digest, "agent_run_id": self.agent_run_id,
                "approval_id": self.approval_id, "event_id": self.event_id,
                "contracts_ready": self.contracts_ready, "revoked": self.revoked}


@dataclass(frozen=True, slots=True)
class DesiredRevision:
    resource_key: str
    revision: int
    spec: Mapping[str, object]
    provenance: Provenance
    evidence: DependencyEvidence
    digest: str = ""
    operations: tuple[MaterializationOperation, ...] = ()
    owner_id: str = ""

    def __post_init__(self) -> None:
        if not self.resource_key.strip() or self.revision < 1:
            raise ValueError("resource key and positive desired revision are required")
        spec = _freeze(self.spec)
        _reject_secret_values(spec)
        operations = tuple(self.operations)
        if len({op.operation_id for op in operations}) != len(operations):
            raise ValueError("operation ids must be unique per revision")
        owner = self.owner_id.strip() or self.resource_key
        calculated = canonical_digest({
            "resource_key": self.resource_key, "revision": self.revision, "spec": spec,
            "operations": [op.canonical() for op in sorted(operations, key=lambda item: (item.operation_id, item.target_ref))],
            "provenance": self.provenance.canonical(), "owner_id": owner,
        })
        supplied = self.digest
        if supplied and supplied != calculated:
            raise ValueError("desired revision digest mismatch")
        object.__setattr__(self, "spec", spec)
        object.__setattr__(self, "operations", operations)
        object.__setattr__(self, "owner_id", owner)
        object.__setattr__(self, "digest", calculated)


@dataclass(frozen=True, slots=True)
class OwnershipEntry:
    operation_id: str
    target_ref: str
    operation_digest: str
    required: bool

    def __post_init__(self) -> None:
        if not self.operation_id.strip() or not self.target_ref.strip() or not _SHA256.fullmatch(self.operation_digest):
            raise ValueError("invalid ownership entry")

    def canonical(self) -> dict[str, object]:
        return {"operation_id": self.operation_id, "target_ref": self.target_ref,
                "operation_digest": self.operation_digest, "required": self.required}


@dataclass(frozen=True, slots=True)
class OwnershipManifest:
    resource_key: str
    owner_id: str
    entries: tuple[OwnershipEntry, ...]
    digest: str = ""

    def __post_init__(self) -> None:
        if not self.resource_key.strip() or not self.owner_id.strip():
            raise ValueError("manifest resource and owner are required")
        entries = tuple(sorted(self.entries, key=lambda item: (item.operation_id, item.target_ref)))
        keys = [(item.operation_id, item.target_ref) for item in entries]
        if len(keys) != len(set(keys)):
            raise ValueError("ownership entries must be unique")
        calculated = canonical_digest({"resource_key": self.resource_key, "owner_id": self.owner_id,
                                       "entries": [item.canonical() for item in entries]})
        if self.digest and self.digest != calculated:
            raise ValueError("ownership manifest digest mismatch")
        object.__setattr__(self, "entries", entries)
        object.__setattr__(self, "digest", calculated)

    @classmethod
    def from_desired(cls, desired: DesiredRevision) -> "OwnershipManifest":
        return cls(desired.resource_key, desired.owner_id, tuple(
            OwnershipEntry(op.operation_id, op.target_ref, op.digest, op.required) for op in desired.operations
        ))

    @classmethod
    def from_operations(cls, resource_key: str, owner_id: str,
                        operations: tuple[MaterializationOperation, ...]) -> "OwnershipManifest":
        return cls(resource_key, owner_id, tuple(
            OwnershipEntry(op.operation_id, op.target_ref, op.digest, op.required) for op in operations
        ))


@dataclass(frozen=True, slots=True)
class PlannedOperation:
    action: OperationAction
    operation: MaterializationOperation
    previous_operation: MaterializationOperation | None = None

    @property
    def required(self) -> bool:
        return self.operation.required

    @property
    def digest(self) -> str:
        return canonical_digest({"action": self.action, "operation": self.operation.canonical(),
                                "previous_digest": self.previous_operation.digest if self.previous_operation else None})

    def canonical(self) -> dict[str, object]:
        return {"action": self.action, "operation": self.operation.canonical(),
                "previous_digest": self.previous_operation.digest if self.previous_operation else None,
                "digest": self.digest}


@dataclass(frozen=True, slots=True)
class MaterializationPlan:
    resource_key: str
    action: PlanAction
    expected_generation: int
    next_generation: int
    desired_revision: int
    desired_digest: str
    desired_manifest: OwnershipManifest
    prior_manifest: OwnershipManifest
    operations: tuple[PlannedOperation, ...]
    blockers: tuple[str, ...]
    evidence_digest: str
    idempotency_key: str
    plan_digest: str
    rollback_of_generation: int | None = None


@dataclass(frozen=True, slots=True)
class RuntimeDiagnostic:
    phase: str
    reason_code: str
    operation_id: str | None = None
    required: bool | None = None

    def __post_init__(self) -> None:
        if not self.phase.strip():
            raise ValueError("diagnostic phase is required")
        object.__setattr__(self, "reason_code", _safe_code(self.reason_code, "unspecified_failure"))
        object.__setattr__(self, "phase", _safe_code(self.phase, "unknown_phase"))
        if self.operation_id is not None and not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", self.operation_id):
            object.__setattr__(self, "operation_id", None)


@dataclass(frozen=True, slots=True)
class AppliedRevision:
    resource_key: str
    desired_revision: int
    desired_digest: str
    generation: int
    image_digest: str
    plan_digest: str
    idempotency_key: str
    provenance: Provenance
    spec: Mapping[str, object]
    operations: tuple[MaterializationOperation, ...]
    manifest: OwnershipManifest
    rollback_of_generation: int | None = None

    def __post_init__(self) -> None:
        if (not self.resource_key.strip() or self.desired_revision < 1 or self.generation < 1
                or not self.idempotency_key.strip() or not _SHA256.fullmatch(self.image_digest)
                or not _SHA256.fullmatch(self.desired_digest) or not _SHA256.fullmatch(self.plan_digest)):
            raise ValueError("invalid applied revision")
        spec = _freeze(self.spec)
        _reject_secret_values(spec)
        object.__setattr__(self, "spec", spec)
        object.__setattr__(self, "operations", tuple(self.operations))
        if self.manifest.resource_key != self.resource_key:
            raise ValueError("applied ownership manifest resource mismatch")


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    resource_key: str
    idempotency_key: str
    plan_digest: str
    generation: int
    phase: RevisionPhase
    outcome: ApplyOutcome
    applied_revision: AppliedRevision | None
    diagnostics: tuple[RuntimeDiagnostic, ...] = ()
    rollback_of_generation: int | None = None


@dataclass(frozen=True, slots=True)
class RuntimeRevisionState:
    resource_key: str
    generation: int = 0
    phase: RevisionPhase = RevisionPhase.IDLE
    applied_revision: AppliedRevision | None = None
    owned_operations: tuple[MaterializationOperation, ...] = ()
    uncertain_operations: tuple[PlannedOperation, ...] = ()
    diagnostics: tuple[RuntimeDiagnostic, ...] = ()
    ownership_manifest: OwnershipManifest | None = None


    def __post_init__(self) -> None:
        if not self.resource_key.strip() or self.generation < 0:
            raise ValueError("invalid runtime state")
        object.__setattr__(self, "phase", RevisionPhase(self.phase))
        object.__setattr__(self, "owned_operations", tuple(self.owned_operations))
        object.__setattr__(self, "uncertain_operations", tuple(self.uncertain_operations))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        if self.ownership_manifest is not None and self.ownership_manifest.resource_key != self.resource_key:
            raise ValueError("ownership manifest resource mismatch")
        if self.applied_revision and self.applied_revision.resource_key != self.resource_key:
            raise ValueError("applied revision resource mismatch")


@dataclass(frozen=True, slots=True)
class PreparedRevision:
    desired: DesiredRevision
    plan: MaterializationPlan
    state: RuntimeRevisionState


@dataclass(frozen=True, slots=True)
class OperationValidation:
    operation_id: str
    ready: bool
    reason_code: str = "ready"


@dataclass(frozen=True, slots=True)
class ValidationReport:
    prepared: PreparedRevision
    valid: bool
    diagnostics: tuple[RuntimeDiagnostic, ...]
    checked_at: datetime
    skipped_optional: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ApplyClaim:
    resource_key: str
    generation: int
    fence_token: str
    idempotency_key: str
    plan_digest: str

    def __post_init__(self) -> None:
        if (not self.resource_key.strip() or self.generation < 1 or not self.fence_token.strip()
                or not self.idempotency_key.strip() or not _SHA256.fullmatch(self.plan_digest)):
            raise ValueError("invalid apply claim")


@dataclass(frozen=True, slots=True)
class ClaimResult:
    status: ClaimStatus
    claim: ApplyClaim | None = None
    prior_attempt: AttemptRecord | None = None
    reason_code: str = "claimed"


@dataclass(frozen=True, slots=True)
class OperationReceipt:
    operation_id: str
    operation_digest: str
    owner_id: str
    fence_token: str
    fence_generation: int
    succeeded: bool
    reason_code: str = "applied"
    effect_unknown: bool = False

    def __post_init__(self) -> None:
        if not self.operation_id.strip() or not self.owner_id.strip() or not self.fence_token.strip() or self.fence_generation < 1:
            raise ValueError("invalid operation receipt")
        object.__setattr__(self, "reason_code", _safe_code(self.reason_code))


@dataclass(frozen=True, slots=True)
class RuntimeTargetReadback:
    """Content observed from one owned target in the backend."""

    operation_id: str
    target_ref: str
    present: bool
    content_digest: str | None

    def __post_init__(self) -> None:
        if not self.operation_id.strip() or not self.target_ref.strip() or not isinstance(self.present, bool):
            raise ValueError("runtime target readback identity and presence are required")
        if self.present:
            if not isinstance(self.content_digest, str) or not _SHA256.fullmatch(self.content_digest):
                raise ValueError("present target requires an observed content digest")
        elif self.content_digest is not None:
            raise ValueError("absent target cannot carry a content digest")


@dataclass(frozen=True, slots=True)
class RuntimeReadback:
    """Trusted read-only proof of applied Pod, bridge and workspace state."""

    resource_key: str
    owner_id: str
    generation: int
    fence_token: str | None
    desired_digest: str
    ownership_digest: str
    image_digest: str
    provenance_digest: str
    bridge_lease_ref: str
    bridge_lease_generation: int
    observed_spec_digest: str
    runtime_ready: bool
    workspace_hydrated: bool
    bridge_lease_active: bool
    targets: tuple[RuntimeTargetReadback, ...]

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (
            self.resource_key, self.owner_id, self.bridge_lease_ref,
        )):
            raise ValueError("runtime readback identity is required")
        if (self.generation < 1 or self.bridge_lease_generation < 1
                or (self.fence_token is not None and not self.fence_token.strip())):
            raise ValueError("runtime readback generation or fence is invalid")
        if any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in (
            self.desired_digest, self.ownership_digest, self.image_digest,
            self.provenance_digest, self.observed_spec_digest,
        )):
            raise ValueError("runtime readback digests must be immutable sha256 values")
        if any(not isinstance(value, bool) for value in (
            self.runtime_ready, self.workspace_hydrated, self.bridge_lease_active,
        )):
            raise ValueError("runtime readback states must be boolean")
        targets = tuple(self.targets)
        if any(not isinstance(item, RuntimeTargetReadback) for item in targets):
            raise ValueError("runtime target readbacks must be typed")
        keys = [(item.operation_id, item.target_ref) for item in targets]
        if len(keys) != len(set(keys)):
            raise ValueError("runtime target readbacks must be unique")
        object.__setattr__(self, "targets", targets)


@dataclass(frozen=True, slots=True)
class ApplyResult:
    outcome: ApplyOutcome
    applied: AppliedRevision | None
    state: RuntimeRevisionState | None
    diagnostics: tuple[RuntimeDiagnostic, ...]
    reason_code: str
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class RollbackCommand:
    resource_key: str
    target_generation: int
    expected_generation: int
    approval_id: str | None
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class RuntimeAuditRecord:
    event: str
    outcome: str
    reason_code: str
    resource_digest: str
    correlation_digest: str
    plan_digest: str
    generation: int


class RuntimeRevisionStorePort(Protocol):
    """Must implement atomic idempotency claim and generation/fence CAS."""
    async def load_state(self, *, resource_key: str) -> RuntimeRevisionState | None: ...
    async def find_attempt(self, *, resource_key: str, idempotency_key: str) -> AttemptRecord | None: ...
    async def load_applied_revision(self, *, resource_key: str, generation: int) -> AppliedRevision | None: ...
    async def claim(self, *, plan: MaterializationPlan, expected_generation: int) -> ClaimResult: ...
    async def finish(self, *, claim: ApplyClaim, state: RuntimeRevisionState, attempt: AttemptRecord) -> bool: ...


class RuntimeOperationValidationPort(Protocol):
    """Read-only operation validation; no runtime or persistence writes."""
    async def validate_operation(self, *, resource_key: str, operation: PlannedOperation,
                                 manifest: OwnershipManifest, prior_manifest: OwnershipManifest,
                                 evidence: DependencyEvidence,
                                 expected_generation: int) -> OperationValidation: ...


class RuntimeEvidenceValidationPort(Protocol):
    """Trusted read-only proof of live lease, projection and provenance evidence."""
    async def validate_evidence(self, *, resource_key: str, desired: DesiredRevision,
                                at: datetime) -> tuple[str, ...]: ...


class RuntimeOperationPort(Protocol):
    """Adapter must verify live lease and enforce the supplied monotonic fence."""
    async def apply_operation(self, *, planned: PlannedOperation, manifest: OwnershipManifest,
                              prior_manifest: OwnershipManifest, evidence: DependencyEvidence,
                              claim: ApplyClaim) -> OperationReceipt: ...
    async def compensate_operation(self, *, planned: PlannedOperation, prior_manifest: OwnershipManifest,
                                   evidence: DependencyEvidence,
                                   claim: ApplyClaim) -> OperationReceipt: ...



class RuntimeReadbackPort(Protocol):
    """Read the live Pod, workspace, bridge lease and owned resource contents."""

    async def read_runtime(
        self, *, desired: DesiredRevision, applied: AppliedRevision,
        absent_operations: tuple[PlannedOperation, ...], evidence: DependencyEvidence,
        fence_token: str | None,
    ) -> RuntimeReadback: ...


class RedactedRuntimeAuditPort(Protocol):
    async def append(self, *, record: RuntimeAuditRecord) -> None: ...


class RuntimeClockPort(Protocol):
    """Trusted current time used for the apply-time lease re-check."""
    async def now(self) -> datetime: ...


def _op_key(op: MaterializationOperation) -> tuple[str, str]:
    return op.operation_id, op.target_ref


def _make_plan(desired: DesiredRevision, state: RuntimeRevisionState, *, idempotency_key: str,
               force: bool = False, rollback_of_generation: int | None = None) -> MaterializationPlan:
    if not idempotency_key.strip():
        raise ValueError("idempotency_key is required")
    blockers: list[str] = []
    if state.resource_key != desired.resource_key:
        blockers.append("resource_state_mismatch")
    blockers.extend(desired.evidence.static_blockers(desired))
    if state.phase is RevisionPhase.APPLYING:
        blockers.append("revision_apply_in_progress")
    current = state.applied_revision
    prior_manifest = state.ownership_manifest or (current.manifest if current else None)
    if prior_manifest and prior_manifest.owner_id != desired.owner_id:
        blockers.append("ownership_conflict")
    if current and rollback_of_generation is None and desired.revision < current.desired_revision:
        blockers.append("stale_desired_revision")
    owned = {_op_key(op): op for op in state.owned_operations}
    uncertain = {_op_key(item.operation): item for item in state.uncertain_operations}
    wanted = {_op_key(op): op for op in desired.operations}
    operations: list[PlannedOperation] = []
    for key, op in wanted.items():
        previous = owned.get(key)
        if force or key in uncertain or previous is None or previous.digest != op.digest:
            operations.append(PlannedOperation(OperationAction.UPSERT, op, previous))
    for key, op in owned.items():
        if key not in wanted:
            operations.append(PlannedOperation(OperationAction.REMOVE, op, op))
    for key, item in uncertain.items():
        if key not in wanted and key not in owned:
            operations.append(PlannedOperation(OperationAction.REMOVE, item.operation))
    ordered = tuple(sorted(operations, key=lambda item: (item.action.value,
                                                           item.operation.operation_id, item.operation.target_ref,
                                                           not item.required)))
    manifest = OwnershipManifest.from_desired(desired)
    if prior_manifest is None:
        prior_manifest = OwnershipManifest(desired.resource_key, desired.owner_id, ())
    no_op = bool(current and current.desired_digest == desired.digest and current.manifest.digest == manifest.digest
                 and not ordered and not state.uncertain_operations
                 and state.phase in {RevisionPhase.APPLIED, RevisionPhase.ROLLED_BACK})
    if blockers:
        action = PlanAction.BLOCKED
    elif rollback_of_generation is not None:
        action = PlanAction.ROLLBACK
    elif no_op:
        action = PlanAction.NOOP
    elif state.phase is RevisionPhase.FAILED or (state.diagnostics and ordered):
        action = PlanAction.RETRY
    elif current is None:
        action = PlanAction.CREATE
    else:
        action = PlanAction.REPLACE
    next_generation = state.generation if action in {PlanAction.NOOP, PlanAction.BLOCKED} else state.generation + 1
    evidence_digest = canonical_digest(desired.evidence.canonical())
    content = {"resource_key": desired.resource_key, "action": action,
               "expected_generation": state.generation, "next_generation": next_generation,
               "desired_revision": desired.revision, "desired_digest": desired.digest,
               "manifest_digest": manifest.digest, "prior_manifest_digest": prior_manifest.digest,
               "operations": [op.canonical() for op in ordered],
               "blockers": tuple(blockers), "evidence_digest": evidence_digest,
               "idempotency_key": idempotency_key, "rollback_of_generation": rollback_of_generation}
    return MaterializationPlan(desired.resource_key, action, state.generation, next_generation,
                               desired.revision, desired.digest, manifest, prior_manifest, ordered,
                               tuple(blockers), evidence_digest, idempotency_key,
                               canonical_digest(content), rollback_of_generation)


class RuntimeMaterializationService:
    """Coordinates deterministic prepare, validation, apply, replay and rollback."""
    def __init__(self, *, store: RuntimeRevisionStorePort, validator: RuntimeOperationValidationPort,
                 runtime: RuntimeOperationPort, audit: RedactedRuntimeAuditPort,
                 evidence_validator: RuntimeEvidenceValidationPort,
                 readback: RuntimeReadbackPort | None = None,
                 clock: RuntimeClockPort | None = None) -> None:
        self._store, self._validator, self._runtime, self._audit, self._clock = (
            store, validator, runtime, audit, clock
        )
        self._evidence_validator = evidence_validator
        self._readback = readback

    async def prepare(self, desired: DesiredRevision, *, idempotency_key: str) -> PreparedRevision:
        loaded = await self._store.load_state(resource_key=desired.resource_key)
        state = loaded or RuntimeRevisionState(desired.resource_key)
        return PreparedRevision(desired, _make_plan(desired, state, idempotency_key=idempotency_key), state)

    async def validate(self, prepared: PreparedRevision, *, at: datetime) -> ValidationReport:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("validation time must be timezone-aware")
        plan, desired = prepared.plan, prepared.desired
        diagnostics = [RuntimeDiagnostic("prepare", reason) for reason in plan.blockers]
        diagnostics.extend(RuntimeDiagnostic("evidence", reason) for reason in desired.evidence.blockers(desired, at=at))
        try:
            trusted_blockers = await self._evidence_validator.validate_evidence(
                resource_key=plan.resource_key, desired=desired, at=at
            )
        except Exception:
            trusted_blockers = ("trusted_evidence_port_error",)
        diagnostics.extend(RuntimeDiagnostic("evidence", _safe_code(reason, "trusted_evidence_rejected"))
                           for reason in trusted_blockers)
        if diagnostics:
            return ValidationReport(prepared, False, tuple(diagnostics), at)
        if plan.action is PlanAction.NOOP:
            return ValidationReport(prepared, True, (), at)
        skipped_optional: list[str] = []
        required_validation_failed = False
        for planned in plan.operations:
            try:
                result = await self._validator.validate_operation(
                    resource_key=plan.resource_key, operation=planned, manifest=plan.desired_manifest,
                    prior_manifest=plan.prior_manifest, evidence=desired.evidence,
                    expected_generation=plan.expected_generation)
            except Exception:
                diagnostics.append(RuntimeDiagnostic("validate", "validation_port_error",
                                                     planned.operation.operation_id, planned.required))
                if planned.required:
                    required_validation_failed = True
                else:
                    skipped_optional.append(planned.operation.operation_id)
                continue
            if result.operation_id != planned.operation.operation_id:
                diagnostics.append(RuntimeDiagnostic("validate", "validation_identity_mismatch",
                                                     planned.operation.operation_id, planned.required))
                required_validation_failed = required_validation_failed or planned.required
            elif not result.ready:
                diagnostics.append(RuntimeDiagnostic("validate", _safe_code(result.reason_code),
                                                     planned.operation.operation_id, planned.required))
                if planned.required:
                    required_validation_failed = True
                else:
                    skipped_optional.append(planned.operation.operation_id)
        return ValidationReport(prepared, not required_validation_failed, tuple(diagnostics), at,
                                tuple(skipped_optional))

    async def apply(self, report: ValidationReport) -> ApplyResult:
        prepared, plan, desired = report.prepared, report.prepared.plan, report.prepared.desired
        if not report.valid:
            return ApplyResult(ApplyOutcome.BLOCKED, None, prepared.state, report.diagnostics, "validation_failed")
        recomputed = _make_plan(desired, prepared.state, idempotency_key=plan.idempotency_key,
                                force=plan.action is PlanAction.ROLLBACK,
                                rollback_of_generation=plan.rollback_of_generation)
        if recomputed.plan_digest != plan.plan_digest:
            return ApplyResult(ApplyOutcome.CONFLICT, None, prepared.state,
                               (RuntimeDiagnostic("apply", "plan_binding_mismatch"),), "plan_binding_mismatch")
        prior = await self.replay(plan.resource_key, plan.idempotency_key, plan_digest=plan.plan_digest)
        if prior is not None:
            return prior
        if self._clock is None:
            return ApplyResult(ApplyOutcome.BLOCKED, None, prepared.state,
                               (RuntimeDiagnostic("evidence", "apply_clock_port_missing"),),
                               "apply_clock_port_missing")
        try:
            apply_time = await self._clock.now()
        except Exception:
            return ApplyResult(ApplyOutcome.BLOCKED, None, prepared.state,
                               (RuntimeDiagnostic("evidence", "apply_clock_port_error"),),
                               "apply_clock_port_error")
        current_blockers = desired.evidence.blockers(desired, at=apply_time)
        try:
            trusted_blockers = await self._evidence_validator.validate_evidence(
                resource_key=plan.resource_key, desired=desired, at=apply_time
            )
        except Exception:
            trusted_blockers = ("trusted_evidence_port_error",)
        current_blockers = tuple(dict.fromkeys(current_blockers + tuple(
            _safe_code(reason, "trusted_evidence_rejected") for reason in trusted_blockers
        )))
        if current_blockers:
            return ApplyResult(ApplyOutcome.BLOCKED, None, prepared.state,
                               tuple(RuntimeDiagnostic("evidence", code) for code in current_blockers), "evidence_stale")
        if plan.action is PlanAction.BLOCKED:
            return ApplyResult(ApplyOutcome.BLOCKED, None, prepared.state,
                               tuple(RuntimeDiagnostic("prepare", code) for code in plan.blockers), "plan_blocked")
        if plan.action is PlanAction.NOOP:
            try:
                latest = await self._store.load_state(resource_key=plan.resource_key)
            except Exception:
                return ApplyResult(ApplyOutcome.BLOCKED, None, prepared.state,
                                   (RuntimeDiagnostic("apply", "revision_store_read_failed"),),
                                   "revision_store_read_failed")
            if (latest is None or latest.generation != plan.expected_generation
                    or latest.phase not in {RevisionPhase.APPLIED, RevisionPhase.ROLLED_BACK}
                    or latest.applied_revision is None
                    or latest.applied_revision.desired_digest != plan.desired_digest
                    or latest.applied_revision.manifest.digest != plan.desired_manifest.digest):
                return ApplyResult(ApplyOutcome.CONFLICT, None, latest or prepared.state,
                                   (RuntimeDiagnostic("apply", "noop_state_stale"),), "noop_state_stale")
            current = latest
            return await self._audit_result(ApplyResult(ApplyOutcome.REPLAY, current.applied_revision,
                                                        current, (), "already_materialized", True),
                                            desired, plan, current.generation,
                                            "runtime.materialization.noop")
        try:
            latest = await self._store.load_state(resource_key=plan.resource_key)
        except Exception:
            return ApplyResult(ApplyOutcome.BLOCKED, None, prepared.state,
                               (RuntimeDiagnostic("apply", "revision_store_read_failed"),),
                               "revision_store_read_failed")
        observed_generation = latest.generation if latest is not None else 0
        if observed_generation != plan.expected_generation:
            return ApplyResult(ApplyOutcome.CONFLICT, None, latest or prepared.state,
                               (RuntimeDiagnostic("apply", "generation_conflict"),), "generation_conflict")
        if not await self._append_audit(self._audit_record(
            "runtime.materialization.started", "started", "apply_claim_requested", desired, plan, plan.next_generation
        )):
            return ApplyResult(ApplyOutcome.BLOCKED, None, prepared.state,
                               (RuntimeDiagnostic("audit", "audit_write_failed"),), "audit_write_failed")
        try:
            claimed = await self._store.claim(plan=plan, expected_generation=plan.expected_generation)
        except Exception:
            return ApplyResult(ApplyOutcome.BLOCKED, None, prepared.state,
                               (RuntimeDiagnostic("claim", "claim_port_error"),), "claim_port_error")
        if claimed.status is ClaimStatus.REPLAY and claimed.prior_attempt:
            return await self._from_attempt(claimed.prior_attempt, desired, plan)
        claim = claimed.claim
        if claimed.status is not ClaimStatus.CLAIMED or claim is None:
            code = _safe_code(claimed.reason_code, "generation_conflict")
            return ApplyResult(ApplyOutcome.CONFLICT, None, prepared.state,
                               (RuntimeDiagnostic("claim", code),), code)
        if (claim.resource_key != plan.resource_key or claim.generation != plan.next_generation
                or claim.idempotency_key != plan.idempotency_key or claim.plan_digest != plan.plan_digest
                or not claim.fence_token.strip()):
            return ApplyResult(ApplyOutcome.CONFLICT, None, prepared.state,
                               (RuntimeDiagnostic("claim", "claim_binding_mismatch"),), "claim_binding_mismatch")

        owned = {_op_key(op): op for op in prepared.state.owned_operations}
        uncertain = {_op_key(item.operation): item for item in prepared.state.uncertain_operations}
        completed: list[PlannedOperation] = []
        diagnostics: list[RuntimeDiagnostic] = []
        fatal_failure = False
        for planned in plan.operations:
            if planned.operation.operation_id in report.skipped_optional:
                diagnostics.append(RuntimeDiagnostic("validate", "optional_operation_not_ready",
                                                     planned.operation.operation_id, False))
                continue
            try:
                receipt = await self._runtime.apply_operation(planned=planned, manifest=plan.desired_manifest,
                                                              prior_manifest=plan.prior_manifest,
                                                              evidence=desired.evidence, claim=claim)
            except Exception:
                uncertain[_op_key(planned.operation)] = planned
                diagnostics.append(RuntimeDiagnostic("apply", "runtime_effect_unknown",
                                                     planned.operation.operation_id, planned.required))
                fatal_failure = True
                break
            if not self._receipt_matches(receipt, planned, claim, plan.desired_manifest.owner_id):
                uncertain[_op_key(planned.operation)] = planned
                diagnostics.append(RuntimeDiagnostic("apply", "operation_receipt_or_fence_mismatch",
                                                     planned.operation.operation_id, planned.required))
                fatal_failure = True
                break
            if not receipt.succeeded:
                diagnostics.append(RuntimeDiagnostic("apply", receipt.reason_code,
                                                     planned.operation.operation_id, planned.required))
                if receipt.effect_unknown:
                    uncertain[_op_key(planned.operation)] = planned
                    fatal_failure = True
                elif planned.required:
                    fatal_failure = True
                # A known optional failure is a warning; it does not prevent
                # later independent operations from being attempted.
                if fatal_failure:
                    break
                continue
            self._record(owned, planned)
            uncertain.pop(_op_key(planned.operation), None)
            completed.append(planned)

        if fatal_failure:
            for planned in reversed(completed):
                try:
                    receipt = await self._runtime.compensate_operation(planned=planned,
                                                                        prior_manifest=plan.prior_manifest,
                                                                        evidence=desired.evidence, claim=claim)
                except Exception:
                    uncertain[_op_key(planned.operation)] = planned
                    diagnostics.append(RuntimeDiagnostic("compensate", "compensation_outcome_unknown",
                                                         planned.operation.operation_id, planned.required))
                    continue
                if not self._receipt_matches(receipt, planned, claim, plan.desired_manifest.owner_id):
                    uncertain[_op_key(planned.operation)] = planned
                    diagnostics.append(RuntimeDiagnostic("compensate", "compensation_receipt_or_fence_mismatch",
                                                         planned.operation.operation_id, planned.required))
                elif receipt.succeeded:
                    self._restore(owned, planned)
                    uncertain.pop(_op_key(planned.operation), None)
                else:
                    uncertain[_op_key(planned.operation)] = planned
                    diagnostics.append(RuntimeDiagnostic("compensate", receipt.reason_code,
                                                         planned.operation.operation_id, planned.required))
            failed = RuntimeRevisionState(plan.resource_key, claim.generation, RevisionPhase.FAILED,
                                          prepared.state.applied_revision,
                                          tuple(sorted(owned.values(), key=_op_key)),
                                          tuple(uncertain.values()), tuple(diagnostics),
                                          prepared.state.ownership_manifest or plan.prior_manifest)
            attempt = AttemptRecord(plan.resource_key, plan.idempotency_key, plan.plan_digest,
                                    claim.generation, RevisionPhase.FAILED, ApplyOutcome.FAILED,
                                    None, tuple(diagnostics))
            return await self._finish(claim, failed, attempt, desired, plan)

        actual_operations = tuple(sorted(owned.values(), key=_op_key))
        actual_manifest = OwnershipManifest.from_operations(plan.resource_key, desired.owner_id, actual_operations)
        applied = AppliedRevision(plan.resource_key, desired.revision, desired.digest, claim.generation,
                                  desired.evidence.image_digest or "", plan.plan_digest, plan.idempotency_key,
                                  desired.provenance, desired.spec, actual_operations, actual_manifest,
                                  plan.rollback_of_generation)
        phase = RevisionPhase.ROLLED_BACK if plan.action is PlanAction.ROLLBACK else RevisionPhase.APPLIED
        state = RuntimeRevisionState(plan.resource_key, claim.generation, phase, applied,
                                     actual_operations,
                                     diagnostics=tuple(diagnostics),
                                     ownership_manifest=actual_manifest)
        outcome = ApplyOutcome.ROLLED_BACK if phase is RevisionPhase.ROLLED_BACK else ApplyOutcome.APPLIED
        attempt = AttemptRecord(plan.resource_key, plan.idempotency_key, plan.plan_digest,
                                claim.generation, phase, outcome, applied, tuple(diagnostics),
                                plan.rollback_of_generation)
        return await self._finish(claim, state, attempt, desired, plan)

    async def failure(
        self,
        *,
        desired: DesiredRevision,
        plan: MaterializationPlan,
        claim: ApplyClaim,
        diagnostics: tuple[RuntimeDiagnostic, ...],
        uncertain_operations: tuple[PlannedOperation, ...] = (),
    ) -> ApplyResult:
        """Persist a fenced failed attempt without granting runtime authority.

        This is the recovery entry point for an adapter that cannot return an
        operation outcome. Diagnostics are already redacted value objects and
        uncertain operations remain explicit so the next prepare produces RETRY.
        """
        if (plan.resource_key != desired.resource_key or claim.resource_key != plan.resource_key
                or claim.generation != plan.next_generation or claim.idempotency_key != plan.idempotency_key
                or claim.plan_digest != plan.plan_digest or not claim.fence_token.strip()):
            return ApplyResult(ApplyOutcome.CONFLICT, None, None,
                               (RuntimeDiagnostic("failure", "claim_binding_mismatch"),), "claim_binding_mismatch")
        prior = await self.replay(plan.resource_key, plan.idempotency_key, plan_digest=plan.plan_digest)
        if prior is not None:
            return prior
        try:
            loaded = await self._store.load_state(resource_key=plan.resource_key)
        except Exception:
            loaded = None
        state = loaded or RuntimeRevisionState(plan.resource_key)
        if state.generation != plan.expected_generation:
            return ApplyResult(ApplyOutcome.CONFLICT, None, state,
                               (RuntimeDiagnostic("failure", "generation_conflict"),), "generation_conflict")
        safe_diagnostics = tuple(item if isinstance(item, RuntimeDiagnostic)
                                 else RuntimeDiagnostic("failure", "invalid_diagnostic")
                                 for item in diagnostics)
        failed = RuntimeRevisionState(
            plan.resource_key,
            claim.generation,
            RevisionPhase.FAILED,
            state.applied_revision,
            state.owned_operations,
            tuple(uncertain_operations),
            safe_diagnostics,
            state.ownership_manifest or plan.prior_manifest,
        )
        attempt = AttemptRecord(plan.resource_key, plan.idempotency_key, plan.plan_digest,
                                claim.generation, RevisionPhase.FAILED, ApplyOutcome.FAILED,
                                None, safe_diagnostics)
        return await self._finish(claim, failed, attempt, desired, plan)

    async def replay(self, resource_key: str, idempotency_key: str, *,
                     plan_digest: str | None = None,
                     rollback_target_generation: int | None = None) -> ApplyResult | None:
        if not idempotency_key.strip():
            return ApplyResult(ApplyOutcome.BLOCKED, None, None,
                               (RuntimeDiagnostic("replay", "idempotency_key_missing"),), "idempotency_key_missing")
        try:
            attempt = await self._store.find_attempt(resource_key=resource_key, idempotency_key=idempotency_key)
            state = await self._store.load_state(resource_key=resource_key)
        except Exception:
            return ApplyResult(ApplyOutcome.BLOCKED, None, None,
                               (RuntimeDiagnostic("replay", "replay_port_error"),), "replay_port_error")
        if attempt is None:
            return None
        if rollback_target_generation is not None and attempt.rollback_of_generation != rollback_target_generation:
            return ApplyResult(ApplyOutcome.CONFLICT, None, state,
                               (RuntimeDiagnostic("replay", "rollback_idempotency_target_mismatch"),),
                               "rollback_idempotency_target_mismatch")
        if plan_digest is not None and attempt.plan_digest != plan_digest:
            return ApplyResult(ApplyOutcome.CONFLICT, None, state,
                               (RuntimeDiagnostic("replay", "idempotency_key_reused"),), "idempotency_key_reused")
        outcome = ApplyOutcome.REPLAY if attempt.applied_revision else ApplyOutcome.FAILED
        result = ApplyResult(outcome, attempt.applied_revision, state, attempt.diagnostics,
                             "idempotent_replay" if attempt.applied_revision else "replayed_failure", True)
        record = RuntimeAuditRecord("runtime.materialization.replay", outcome.value, result.reason_code,
                                    canonical_digest(resource_key), canonical_digest(idempotency_key),
                                    attempt.plan_digest, attempt.generation)
        if not await self._append_audit(record):
            return ApplyResult(ApplyOutcome.BLOCKED, None, state,
                               (RuntimeDiagnostic("audit", "audit_write_failed"),), "audit_write_failed", True)
        return result

    async def rollback(self, command: RollbackCommand, *, evidence: DependencyEvidence,
                       at: datetime, provenance: Provenance | None = None) -> ApplyResult:
        if not command.idempotency_key.strip():
            return ApplyResult(ApplyOutcome.BLOCKED, None, None,
                               (RuntimeDiagnostic("rollback", "rollback_idempotency_key_missing"),),
                               "rollback_blocked")
        if not command.approval_id or command.approval_id != evidence.approval_id:
            return ApplyResult(ApplyOutcome.BLOCKED, None, None,
                               (RuntimeDiagnostic("rollback", "rollback_approval_missing_or_mismatched"),),
                               "rollback_blocked")
        replayed = await self.replay(command.resource_key, command.idempotency_key,
                                     rollback_target_generation=command.target_generation)
        if replayed is not None:
            return replayed
        try:
            loaded = await self._store.load_state(resource_key=command.resource_key)
        except Exception:
            loaded = None
        state = loaded or RuntimeRevisionState(command.resource_key)
        blockers: list[str] = []
        if command.expected_generation != state.generation:
            blockers.append("generation_conflict")
        if command.target_generation < 1 or command.target_generation >= state.generation:
            blockers.append("rollback_target_or_generation_invalid")

        if state.applied_revision is None:
            blockers.append("rollback_current_revision_missing")
        if blockers:
            return ApplyResult(ApplyOutcome.BLOCKED, None, state,
                               tuple(RuntimeDiagnostic("rollback", code) for code in blockers), "rollback_blocked")
        try:
            target = await self._store.load_applied_revision(resource_key=command.resource_key,
                                                               generation=command.target_generation)
        except Exception:
            target = None
        if target is None or target.generation != command.target_generation:
            return ApplyResult(ApplyOutcome.BLOCKED, None, state,
                               (RuntimeDiagnostic("rollback", "rollback_target_not_found"),), "rollback_target_not_found")
        if target.manifest.owner_id != state.applied_revision.manifest.owner_id:
            return ApplyResult(ApplyOutcome.BLOCKED, None, state,
                               (RuntimeDiagnostic("rollback", "rollback_owner_mismatch"),), "rollback_owner_mismatch")
        desired = DesiredRevision(target.resource_key, target.desired_revision, target.spec,
                                  provenance or target.provenance, evidence, operations=target.operations,
                                  owner_id=target.manifest.owner_id)
        evidence_blockers = evidence.blockers(desired, at=at)
        if evidence_blockers:
            return ApplyResult(ApplyOutcome.BLOCKED, None, state,
                               tuple(RuntimeDiagnostic("rollback", code) for code in evidence_blockers),
                               "rollback_evidence_blocked")
        plan = _make_plan(desired, state, idempotency_key=command.idempotency_key,
                          force=True, rollback_of_generation=target.generation)
        prepared = PreparedRevision(desired, plan, state)
        report = await self.validate(prepared, at=at)
        if not report.valid:
            return ApplyResult(ApplyOutcome.BLOCKED, None, state, report.diagnostics, "rollback_validation_failed")
        return await self.apply(report)

    async def _finish(self, claim: ApplyClaim, state: RuntimeRevisionState, attempt: AttemptRecord,
                      desired: DesiredRevision, plan: MaterializationPlan) -> ApplyResult:
        try:
            saved = await self._store.finish(claim=claim, state=state, attempt=attempt)
        except Exception:
            saved = False
        if not saved:
            return ApplyResult(ApplyOutcome.CONFLICT, None, state,
                               (RuntimeDiagnostic("finish", "stale_generation_fence"),), "stale_generation_fence")
        result = ApplyResult(attempt.outcome, attempt.applied_revision, state, attempt.diagnostics,
                             "required_operation_failed" if attempt.outcome is ApplyOutcome.FAILED else
                             "rolled_back" if attempt.outcome is ApplyOutcome.ROLLED_BACK else "applied")
        return await self._audit_result(result, desired, plan, claim.generation,
                                        "runtime.materialization.finished")

    async def _from_attempt(self, attempt: AttemptRecord, desired: DesiredRevision,
                            plan: MaterializationPlan) -> ApplyResult:
        outcome = ApplyOutcome.REPLAY if attempt.applied_revision else ApplyOutcome.FAILED
        result = ApplyResult(outcome, attempt.applied_revision, None, attempt.diagnostics,
                             "idempotent_replay" if attempt.applied_revision else "replayed_failure", True)
        return await self._audit_result(result, desired, plan, attempt.generation,
                                        "runtime.materialization.replay")

    async def _audit_result(self, result: ApplyResult, desired: DesiredRevision, plan: MaterializationPlan,
                            generation: int, event: str) -> ApplyResult:
        record = self._audit_record(event, result.outcome.value, _safe_code(result.reason_code),
                                    desired, plan, generation)
        if not await self._append_audit(record):
            return ApplyResult(ApplyOutcome.BLOCKED, None, result.state,
                               result.diagnostics + (RuntimeDiagnostic("audit", "audit_write_failed"),),
                               "audit_write_failed", result.replayed)
        return result

    async def _append_audit(self, record: RuntimeAuditRecord) -> bool:
        try:
            await self._audit.append(record=record)
            return True
        except Exception:
            return False

    @staticmethod
    def _audit_record(event: str, outcome: str, reason: str, desired: DesiredRevision,
                      plan: MaterializationPlan, generation: int) -> RuntimeAuditRecord:
        return RuntimeAuditRecord(event, outcome, _safe_code(reason), canonical_digest(desired.resource_key),
                                  canonical_digest(desired.provenance.correlation_id), plan.plan_digest, generation)

    @staticmethod
    def _receipt_matches(receipt: OperationReceipt, planned: PlannedOperation,
                         claim: ApplyClaim, owner_id: str) -> bool:
        return (receipt.operation_id == planned.operation.operation_id
                and receipt.operation_digest == planned.digest
                and receipt.owner_id == owner_id and receipt.fence_token == claim.fence_token
                and receipt.fence_generation == claim.generation)

    @staticmethod
    def _record(owned: dict[tuple[str, str], MaterializationOperation], planned: PlannedOperation) -> None:
        key = _op_key(planned.operation)
        if planned.action is OperationAction.UPSERT:
            owned[key] = planned.operation
        else:
            owned.pop(key, None)

    @staticmethod
    def _restore(owned: dict[tuple[str, str], MaterializationOperation], planned: PlannedOperation) -> None:
        key = _op_key(planned.operation)
        owned.pop(key, None)
        if planned.previous_operation is not None:
            owned[key] = planned.previous_operation


# Preserve the original pure-planner entry point for current callers.
def plan_materialization(desired: DesiredRevision, current: AppliedRevision | None,
                         *, idempotency_key: str) -> MaterializationPlan:
    state = RuntimeRevisionState(desired.resource_key, current.generation if current else 0,
                                 RevisionPhase.APPLIED if current else RevisionPhase.IDLE,
                                 current, current.operations if current else (),
                                 ownership_manifest=current.manifest if current else None)
    return _make_plan(desired, state, idempotency_key=idempotency_key)


__all__ = ["AppliedRevision", "ApplyClaim", "ApplyOutcome", "ApplyResult", "AttemptRecord", "ClaimResult",
           "ClaimStatus", "DependencyEvidence", "DesiredRevision", "MaterializationOperation", "MaterializationPlan",
           "OperationAction", "OperationReceipt", "OperationValidation", "OwnershipEntry", "OwnershipManifest",
           "PlanAction", "PlannedOperation", "PreparedRevision", "Provenance", "RedactedRuntimeAuditPort",
           "RevisionPhase", "RollbackCommand", "RuntimeAuditRecord", "RuntimeDiagnostic",
           "RuntimeMaterializationService", "RuntimeOperationPort", "RuntimeOperationValidationPort",
           "RuntimeClockPort", "RuntimeRevisionState", "RuntimeRevisionStorePort", "ValidationReport", "canonical_digest",
           "plan_materialization"]
