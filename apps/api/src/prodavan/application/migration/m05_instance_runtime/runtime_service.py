"""M05 instance import, exact resolution, lifecycle fencing and M06 handoff.

Application contracts only: durable adapters own atomic evidence/checkpoint/audit
transactions and full-identity compare-and-set. No ORM or live-data adapter lives here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Mapping, Protocol, Sequence

from prodavan.application.dynamic_apps_definition.contracts import DefinitionResolution, DefinitionResolutionOutcome
from prodavan.application.migration.m04_scope.scope_migration import ScopeResolution, ScopeResolutionResult
from prodavan.application.migration.m04e_definition_version.backfill import (
    BackfillOutcome as M04EBackfillOutcome,
    BackfillRecord as M04EEvidenceRecord,
    EvidenceStatus as M04EEvidenceStatus,
    HandoffStatus as M04EHandoffStatus,
    M05EvidenceExport as M04EM05EvidenceExport,
)
from prodavan.application.migration.m04e_definition_version.readiness import (
    UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED,
)
from prodavan.application.scope_registry.contracts import OwnerState, ScopeAnchors, ScopeKind, ScopeRegistryEntry


SOURCE_KIND_MAX_LENGTH = 80
SOURCE_ID_MAX_LENGTH = 80
SOURCE_SCHEMA_VERSION_MAX_LENGTH = 40


def _validate_source_identity(source_kind: str, source_id: str, schema_version: str) -> None:
    values = (source_kind, source_id, schema_version)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("source identity is required")
    if (len(source_kind) > SOURCE_KIND_MAX_LENGTH
        or len(source_id) > SOURCE_ID_MAX_LENGTH
        or len(schema_version) > SOURCE_SCHEMA_VERSION_MAX_LENGTH):
        raise ValueError("source identity exceeds registered ORM column width")


def _validate_source_cursor(source_id: str | None) -> None:
    if source_id is None:
        return
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("checkpoint source cursor cannot be blank")
    if len(source_id) > SOURCE_ID_MAX_LENGTH:
        raise ValueError("checkpoint source cursor exceeds registered ORM source_id width")


def _digest(value: object) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(data.encode("utf-8")).hexdigest()


def _valid_digest(value: str | None) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    raw = value.removeprefix("sha256:")
    return len(raw) == 64 and all(char in "0123456789abcdef" for char in raw)


def _release_evidence_confirmed() -> bool:
    return UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED


class ImportOutcome(StrEnum):
    IMPORTED = "imported"
    REPLAY = "replay"
    CONFLICT = "conflict"
    QUARANTINED = "quarantined"
    BLOCKED = "blocked"


class InstanceLifecycleState(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DISABLED = "disabled"
    REVOKED = "revoked"
    QUARANTINED = "quarantined"


class RuntimeAvailability(StrEnum):
    ACTIVE = "active"
    STOPPED = "stopped"
    ARCHIVED = "archived"
    QUARANTINED = "quarantined"


class HandoffStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class M04ScopeGateEvidence:
    partition: tuple[str, str]
    source_snapshot_digest: str
    canonical_digest: str
    checkpoint_digest: str
    parent_validation_digest: str
    rls_validation_digest: str
    typed_constraint_digests: tuple[str, ...]
    active_scope_count: int
    unknown_required_mappings: int = 0
    unresolved_parent_rows: int = 0

    def __post_init__(self) -> None:
        if min(self.active_scope_count, self.unknown_required_mappings, self.unresolved_parent_rows) < 0:
            raise ValueError("M04 evidence counts must be non-negative")

    @property
    def ready(self) -> bool:
        digests = (self.source_snapshot_digest, self.canonical_digest, self.checkpoint_digest,
                   self.parent_validation_digest, self.rls_validation_digest, *self.typed_constraint_digests)
        return (len(self.partition) == 2 and all(x.strip() for x in self.partition)
                and bool(self.typed_constraint_digests) and all(_valid_digest(x) for x in digests)
                and self.active_scope_count > 0 and self.unknown_required_mappings == 0
                and self.unresolved_parent_rows == 0)


@dataclass(frozen=True, slots=True)
class M05RuntimeGateEvidence:
    partition: tuple[str, str]
    source_snapshot_digest: str
    canonical_digest: str
    checkpoint_digest: str
    m04_scope_digest: str
    m04e_export_digest: str
    exact_instance_key_digest: str
    registry_to_instance_fk_digest: str
    instance_to_registry_fk_digest: str
    artifact_validation_digest: str
    rls_review_digest: str
    writer_cas_review_digest: str
    runtime_cas_review_digest: str
    stale_worker_denial_digest: str
    audit_atomicity_digest: str
    unresolved_required_instances: int = 0
    quarantined_required_instances: int = 0

    def __post_init__(self) -> None:
        if min(self.unresolved_required_instances, self.quarantined_required_instances) < 0:
            raise ValueError("M05 evidence counts must be non-negative")

    @property
    def schema_ready(self) -> bool:
        evidence = (self.source_snapshot_digest, self.canonical_digest, self.checkpoint_digest,
                    self.m04_scope_digest, self.m04e_export_digest,
                    self.exact_instance_key_digest, self.registry_to_instance_fk_digest,
                    self.instance_to_registry_fk_digest, self.artifact_validation_digest,
                    self.rls_review_digest, self.writer_cas_review_digest,
                    self.runtime_cas_review_digest, self.stale_worker_denial_digest,
                    self.audit_atomicity_digest)
        return (len(self.partition) == 2 and all(x.strip() for x in self.partition)
                and all(_valid_digest(x) for x in evidence))

    @property
    def ready(self) -> bool:
        return (self.schema_ready and self.unresolved_required_instances == 0
                and self.quarantined_required_instances == 0)


@dataclass(frozen=True, slots=True)
class M05UpstreamEvidence:
    m04_scope: M04ScopeGateEvidence
    m04e_export: M04EM05EvidenceExport
    m05_runtime: M05RuntimeGateEvidence

    def ready_for_import(self, partition: tuple[str, str], snapshot_digest: str) -> bool:
        if not _release_evidence_confirmed():
            return False
        e = self.m04e_export
        required_gates = {"MIG-M04-01", "MIG-M04-02", "MIG-M04E-01",
                          "active_typed_parent_scope", "same_definition_version", "immutable_artifact"}
        recorded_gates = {gate.name: gate for gate in e.gates}
        gate_checkpoints = {
            "MIG-M04-01": self.m04_scope.parent_validation_digest,
            "MIG-M04-02": self.m04_scope.canonical_digest,
            "MIG-M04E-01": e.canonical_digest,
            "active_typed_parent_scope": self.m04_scope.canonical_digest,
        }
        gate_evidence_matches = all(
            name in recorded_gates and recorded_gates[name].evidence_digest == digest
            for name, digest in gate_checkpoints.items()
        )
        gate_set_valid = (
            len(recorded_gates) == len(e.gates)
            and required_gates.issubset(recorded_gates)
            and len(recorded_gates) == len(required_gates)
            and all(gate.passed and _valid_digest(gate.evidence_digest)
                    for gate in recorded_gates.values())
        )
        checkpoint = e.checkpoint
        marker = e.handoff_marker
        marker_ready = bool(
            marker is not None
            and marker.record_id.strip() and marker.task_id == "MIG-M04E-02"
            and marker.status is M04EHandoffStatus.READY
            and marker.partition == partition
            and marker.source_snapshot_digest == e.source_snapshot_digest
            and marker.canonical_digest == e.canonical_digest
            and marker.checkpoint == checkpoint
            and marker.accepted_count == len(e.accepted)
            and marker.quarantined_count == len(e.quarantined)
            and marker.gate_evidence == e.gates
            and marker.canonical_positive_writes is False
            and marker.actor.strip() and marker.recorded_at.strip()
            and marker.decision_owner.strip() and marker.correlation_id.strip()
            and marker.writer_generation == checkpoint.writer_generation
        )

        active_scope_digest = recorded_gates.get("active_typed_parent_scope")

        def accepted_record_is_bound(row: object) -> bool:
            if not isinstance(row, M04EEvidenceRecord):
                return False
            proof = dict(row.proof_evidence)
            required_proofs = {"owner", "active_scope"}
            if row.phase == "version":
                required_proofs.update({"same_definition", "immutable_artifact"})
            identity_ok = bool(row.canonical_identity and row.canonical_identity[0] == partition[0])
            if row.phase == "version":
                identity_ok = (identity_ok and len(row.canonical_identity) == 3
                               and row.canonical_identity[1] == partition[1]
                               and bool(row.canonical_identity[2])
                               and _valid_digest(row.artifact_digest))
            elif row.phase == "definition":
                identity_ok = (identity_ok and len(row.canonical_identity) == 2
                               and bool(row.canonical_identity[1]))
            else:
                identity_ok = False
            return bool(
                row.status is M04EEvidenceStatus.ACCEPTED and row.reason_code is None
                and row.tenant_id == partition[0] and row.app_definition_id == partition[1]
                and row.source_snapshot_digest == e.source_snapshot_digest
                and row.checkpoint.partition == partition
                and row.checkpoint.phase == row.phase
                and row.checkpoint.source_snapshot_id == checkpoint.source_snapshot_id
                and row.checkpoint.source_schema_version == checkpoint.source_schema_version
                and row.checkpoint.source_snapshot_digest == e.source_snapshot_digest
                and row.checkpoint.canonical_schema_version == checkpoint.canonical_schema_version
                and row.checkpoint.sequence <= checkpoint.sequence
                and row.writer_generation == checkpoint.writer_generation
                and row.outcome in {None, M04EBackfillOutcome.ACCEPTED}
                and bool(row.transaction_id and row.correlation_id)
                and len(row.correlation_id) > 0
                and row.phase in {"definition", "version"}
                and _valid_digest(row.source_digest) and _valid_digest(row.canonical_digest)
                and identity_ok and len(proof) == len(row.proof_evidence)
                and required_proofs.issubset(proof)
                and all(_valid_digest(proof.get(name, "")) for name in required_proofs)
                and active_scope_digest is not None
                and proof.get("active_scope") == active_scope_digest.evidence_digest
            )

        accepted_keys = {(row.source_kind, row.source_id, row.schema_version) for row in e.accepted}
        accepted_definition_rows = tuple(row for row in e.accepted if row.phase == "definition")
        accepted_version_rows = tuple(row for row in e.accepted if row.phase == "version")
        accepted_correlations = {row.correlation_id for row in e.accepted}
        accepted_canonical_digest = _digest(tuple(sorted(
            (row.source_id, row.canonical_identity, row.canonical_digest) for row in e.accepted
        )))
        definition_ids = {row.source_id for row in accepted_definition_rows}
        return (self.m04_scope.partition == partition and self.m04_scope.ready
                and e.tenant_id == partition[0] and e.app_definition_id == partition[1]
                and checkpoint.partition == partition and checkpoint.phase == "version"
                and checkpoint.phase_complete
                and bool(checkpoint.source_snapshot_id.strip() and checkpoint.source_schema_version.strip()
                         and checkpoint.canonical_schema_version.strip())
                and checkpoint.source_snapshot_digest == e.source_snapshot_digest
                and _valid_digest(e.source_snapshot_digest) and _valid_digest(e.canonical_digest)
                and len(recorded_gates) == len(e.gates) and gate_set_valid and gate_evidence_matches
                and e.status is M04EHandoffStatus.READY and e.canonical_positive_writes is False
                and bool(e.accepted) and not e.quarantined
                and marker is not None and accepted_correlations == {marker.correlation_id}
                and len(accepted_definition_rows) == 1 and bool(accepted_version_rows)
                and {row.canonical_identity[1] for row in accepted_version_rows} == definition_ids
                and len(accepted_keys) == len(e.accepted)
                and accepted_canonical_digest == e.canonical_digest
                and all(accepted_record_is_bound(row) for row in e.accepted)
                and marker_ready
                and self.m05_runtime.m04_scope_digest == self.m04_scope.canonical_digest
                and self.m05_runtime.m04e_export_digest == e.canonical_digest
                and self.m05_runtime.partition == partition
                and self.m05_runtime.schema_ready
                and self.m05_runtime.source_snapshot_digest == snapshot_digest)

    def ready_for_positive_transition(self, partition: tuple[str, str]) -> bool:
        return (self.ready_for_import(partition, self.m05_runtime.source_snapshot_digest)
                and self.m05_runtime.ready)


@dataclass(frozen=True, slots=True)
class InstanceIdentity:
    plugin_instance_id: str
    definition_id: str
    version_id: str
    app_id: str
    manifest_version: str
    tenant_id: str
    scope_kind: ScopeKind
    scope_id: str
    installation_scope: ScopeAnchors
    plugin_instance_scope: ScopeAnchors
    artifact_digest: str
    subject_id: str
    session_id: str

    def __post_init__(self) -> None:
        values = (self.plugin_instance_id, self.definition_id, self.version_id, self.app_id,
                  self.manifest_version, self.tenant_id, self.scope_id, self.subject_id, self.session_id)
        if any(not value.strip() for value in values):
            raise ValueError("instance identity requires exact non-empty bindings")
        if not _valid_digest(self.artifact_digest):
            raise ValueError("artifact_digest must be a sha256 digest")
        if (self.plugin_instance_scope.scope_kind is not ScopeKind.PLUGIN_INSTANCE
            or self.plugin_instance_scope.scope_id != self.plugin_instance_id
            or self.plugin_instance_scope.plugin_instance_id != self.plugin_instance_id
            or self.plugin_instance_scope.tenant_id != self.tenant_id
            or self.plugin_instance_scope.cabinet_id != self.installation_scope.cabinet_id
            or self.plugin_instance_scope.project_id != self.installation_scope.project_id):
            raise ValueError("plugin_instance_scope must prove the exact reciprocal instance relation")
        if self.installation_scope.scope_kind is ScopeKind.PLUGIN_INSTANCE:
            raise ValueError("installation scope must be tenant, cabinet or project")
        if self.installation_scope.tenant_id != self.tenant_id:
            raise ValueError("instance tenant must match installation scope")
        if self.scope_kind is not ScopeKind.PLUGIN_INSTANCE or self.scope_id != self.plugin_instance_id:
            raise ValueError("runtime instance identity must use exact plugin_instance scope")

    @property
    def partition(self) -> tuple[str, str]:
        return self.tenant_id, self.definition_id

    @property
    def digest(self) -> str:
        return _digest({"instance": self.plugin_instance_id, "definition": self.definition_id,
                        "version": self.version_id, "app_id": self.app_id,
                        "manifest_version": self.manifest_version, "tenant": self.tenant_id,
                        "scope_kind": self.scope_kind.value, "scope_id": self.scope_id,
                        "installation": self.installation_scope.model_dump(mode="json"),
                        "plugin_instance_scope": self.plugin_instance_scope.model_dump(mode="json"),
                        "artifact": self.artifact_digest, "subject": self.subject_id,
                        "session": self.session_id})


@dataclass(frozen=True, slots=True)
class InstanceSourceRuntime:
    lifecycle: InstanceLifecycleState
    runtime_generation: int
    writer_generation: int
    availability: RuntimeAvailability = RuntimeAvailability.ACTIVE

    def __post_init__(self) -> None:
        if min(self.runtime_generation, self.writer_generation) < 0:
            raise ValueError("source generations must be non-negative")


@dataclass(frozen=True, slots=True)
class InstanceImportCandidate:
    source_kind: str
    source_id: str
    schema_version: str
    payload: Mapping[str, object]
    identity: InstanceIdentity | None
    source_runtime: InstanceSourceRuntime | None

    def __post_init__(self) -> None:
        _validate_source_identity(self.source_kind, self.source_id, self.schema_version)

    @property
    def digest(self) -> str:
        runtime = None if self.source_runtime is None else (
            getattr(self.source_runtime.lifecycle, "value", str(self.source_runtime.lifecycle)),
            getattr(self.source_runtime.availability, "value", str(self.source_runtime.availability)),
            self.source_runtime.runtime_generation, self.source_runtime.writer_generation)
        return _digest({"source_kind": self.source_kind, "source_id": self.source_id,
                        "schema_version": self.schema_version, "payload": dict(self.payload),
                        "identity": self.identity.digest if self.identity else None, "runtime": runtime})


@dataclass(frozen=True, slots=True)
class ImportCheckpoint:
    partition: tuple[str, str]
    source_snapshot_id: str
    source_schema_version: str
    source_snapshot_digest: str
    after_source_id: str | None = None
    sequence: int = 0

    def __post_init__(self) -> None:
        if len(self.partition) != 2 or any(not x.strip() for x in self.partition):
            raise ValueError("checkpoint requires tenant/definition partition")
        if not self.source_snapshot_id.strip() or not self.source_schema_version.strip():
            raise ValueError("source snapshot identity is required")
        if not _valid_digest(self.source_snapshot_digest) or self.sequence < 0:
            raise ValueError("invalid snapshot digest or checkpoint sequence")
        if (self.sequence == 0) != (self.after_source_id is None):
            raise ValueError("checkpoint cursor and sequence must advance together")
        _validate_source_cursor(self.after_source_id)

    def advance(self, source_id: str) -> "ImportCheckpoint":
        return ImportCheckpoint(self.partition, self.source_snapshot_id, self.source_schema_version,
                                self.source_snapshot_digest, source_id, self.sequence + 1)

    @property
    def digest(self) -> str:
        return _digest((self.partition, self.source_snapshot_id, self.source_schema_version,
                        self.source_snapshot_digest, self.after_source_id, self.sequence))


@dataclass(frozen=True, slots=True)
class SubjectSessionBindingEvidence:
    """Trusted proof that the authenticated subject owns the active source session."""

    tenant_id: str
    subject_id: str
    session_id: str
    session_tenant_id: str
    session_subject_id: str
    subject_active: bool
    session_active: bool
    correlation_id: str
    evidence_digest: str

    def matches_identity(self, identity: InstanceIdentity, *, correlation_id: str) -> bool:
        return bool(
            self.tenant_id == identity.tenant_id
            and self.session_tenant_id == identity.tenant_id
            and self.subject_id == identity.subject_id
            and self.session_subject_id == identity.subject_id
            and self.session_id == identity.session_id
            and self.subject_active and self.session_active
            and self.correlation_id == correlation_id
            and _valid_digest(self.evidence_digest)
        )

    def matches(self, identity: InstanceIdentity, context: TrustedRuntimeContext) -> bool:
        return bool(
            context.usable
            and context.resolved_tenant_id == identity.tenant_id
            and context.subject_id == identity.subject_id
            and context.session_id == identity.session_id
            and self.matches_identity(identity, correlation_id=context.correlation_id)
        )


@dataclass(frozen=True, slots=True)
class InstanceBindingEvidence:
    identity: InstanceIdentity
    source_runtime: InstanceSourceRuntime
    installation_scope: ScopeRegistryEntry
    plugin_instance_scope: ScopeRegistryEntry
    definition_version: DefinitionResolution
    subject_session: SubjectSessionBindingEvidence | None = None


@dataclass(frozen=True, slots=True)
class RuntimeAuditRecord:
    event: str
    outcome: str
    reason_code: str
    correlation_id: str
    runtime_generation: int | None = None
    writer_generation: int | None = None

    def __post_init__(self) -> None:
        if not all(x.strip() for x in (self.event, self.outcome, self.reason_code, self.correlation_id)):
            raise ValueError("audit classification and correlation ID are required")
        if any(x is not None and x < 0 for x in (self.runtime_generation, self.writer_generation)):
            raise ValueError("audit generations must be non-negative")


@dataclass(frozen=True, slots=True)
class InstanceImportRecord:
    source_kind: str
    source_id: str
    schema_version: str
    source_digest: str
    checkpoint: ImportCheckpoint
    outcome: ImportOutcome
    reason_code: str | None
    correlation_id: str
    binding_evidence: InstanceBindingEvidence | None = None
    cursor_checkpoint: ImportCheckpoint | None = None

    def __post_init__(self) -> None:
        _validate_source_identity(self.source_kind, self.source_id, self.schema_version)
        if not _valid_digest(self.source_digest) or not self.correlation_id.strip():
            raise ValueError("import digest and correlation ID are required")
        cursor = self.cursor_checkpoint
        if (cursor is None or cursor.partition != self.checkpoint.partition
            or self.checkpoint.after_source_id != self.source_id
            or cursor.after_source_id != self.source_id
            or cursor.sequence <= 0):
            raise ValueError("mapping and page cursor checkpoints must preserve exact source identity")
        if self.outcome is ImportOutcome.IMPORTED:
            if self.binding_evidence is None:
                raise ValueError("imported evidence requires exact binding proof")
            if self.binding_evidence.subject_session is None:
                raise ValueError("imported evidence requires subject/session proof")
            if not self.binding_evidence.subject_session.matches_identity(
                self.binding_evidence.identity, correlation_id=self.correlation_id
            ):
                raise ValueError("imported evidence subject/session proof differs from source identity")
        if self.outcome in {ImportOutcome.QUARANTINED, ImportOutcome.CONFLICT, ImportOutcome.BLOCKED} and self.binding_evidence:
            raise ValueError("negative outcome cannot expose positive binding evidence")


@dataclass(frozen=True, slots=True)
class QuarantineRetryState:
    attempts: tuple["QuarantineRetryAttempt", ...] = ()

    def __post_init__(self) -> None:
        for index, attempt in enumerate(self.attempts, start=1):
            if attempt.attempt_number != index:
                raise ValueError("retry attempts must be contiguous and monotonic")
            if self.attempts and (
                attempt.source_kind, attempt.source_id, attempt.schema_version,
                attempt.source_digest, attempt.checkpoint,
            ) != (
                self.attempts[0].source_kind, self.attempts[0].source_id,
                self.attempts[0].schema_version, self.attempts[0].source_digest,
                self.attempts[0].checkpoint,
            ):
                raise ValueError("retry history must preserve the exact quarantined source")

    @property
    def latest(self) -> "QuarantineRetryAttempt | None":
        return self.attempts[-1] if self.attempts else None


@dataclass(frozen=True, slots=True)
class QuarantineRetryAttempt:
    source_kind: str
    source_id: str
    schema_version: str
    source_digest: str
    attempt_number: int
    outcome: ImportOutcome
    reason_code: str | None
    correlation_id: str
    checkpoint: ImportCheckpoint
    binding_evidence: InstanceBindingEvidence | None = None

    def __post_init__(self) -> None:
        _validate_source_identity(self.source_kind, self.source_id, self.schema_version)
        if (not _valid_digest(self.source_digest) or self.attempt_number <= 0
            or not self.correlation_id.strip() or self.checkpoint.sequence <= 0
            or self.checkpoint.after_source_id != self.source_id):
            raise ValueError("quarantine retry identity is invalid")
        if self.outcome is ImportOutcome.IMPORTED:
            if self.binding_evidence is None or self.binding_evidence.subject_session is None:
                raise ValueError("accepted retry requires exact subject/session evidence")
            if (self.binding_evidence.identity.partition != self.checkpoint.partition
                or self.binding_evidence.subject_session.correlation_id != self.correlation_id
                or not self.binding_evidence.subject_session.matches_identity(
                    self.binding_evidence.identity, correlation_id=self.correlation_id)):
                raise ValueError("retry binding proof differs from the attempt lineage")
        elif self.binding_evidence is not None:
            raise ValueError("negative retry outcome cannot expose positive binding evidence")


def _validate_retry_state_for_source(
    state: QuarantineRetryState, *, source_kind: str, source_id: str,
    schema_version: str, source_digest: str, checkpoint: ImportCheckpoint,
) -> None:
    for index, attempt in enumerate(state.attempts, start=1):
        _validate_source_identity(attempt.source_kind, attempt.source_id, attempt.schema_version)
        if (attempt.attempt_number != index or attempt.source_kind != source_kind
            or attempt.source_id != source_id or attempt.schema_version != schema_version
            or attempt.source_digest != source_digest or attempt.checkpoint != checkpoint):
            raise ValueError("retry history is not contiguous or source-bound")


class InstanceQuarantineRetryPort(Protocol):
    async def find_quarantine(self, *, partition: tuple[str, str], source_kind: str,
                              source_id: str, schema_version: str) -> InstanceImportRecord | None: ...
    async def load_retry_state(self, *, partition: tuple[str, str], source_kind: str,
                               source_id: str, schema_version: str) -> QuarantineRetryState: ...
    async def find_instance(self, *, partition: tuple[str, str],
                            plugin_instance_id: str) -> InstanceImportRecord | None: ...
    async def commit_retry_attempt(self, *, context: TrustedRuntimeContext,
                                   original: InstanceImportRecord, expected_attempt_count: int,
                                   attempt: QuarantineRetryAttempt,
                                   audit_record: RuntimeAuditRecord) -> QuarantineRetryAttempt:
        """Atomically append one immutable retry attempt and its redacted audit.

        Verify the original quarantined row/digest and expected attempt count under
        tenant RLS. A successful attempt records its mapping without replacing the
        original quarantine. Enforce exact-instance uniqueness and roll back on CAS,
        validation, or audit failure.
        """
        ...


class InstanceExactCandidatePort(Protocol):
    async def read_exact(self, *, partition: tuple[str, str], source_snapshot_id: str,
                         source_schema_version: str, source_snapshot_digest: str,
                         source_kind: str, source_id: str,
                         schema_version: str) -> InstanceImportCandidate | None:
        """Read one row from the immutable pinned snapshot; never accept caller payload."""
        ...


class InstanceImportStore(Protocol):
    async def find(self, *, partition: tuple[str, str], source_kind: str,
                   source_id: str, schema_version: str) -> InstanceImportRecord | None: ...
    async def load_checkpoint(self, *, partition: tuple[str, str], source_snapshot_id: str,
                              source_schema_version: str, source_snapshot_digest: str) -> ImportCheckpoint | None: ...
    async def find_instance(self, *, partition: tuple[str, str],
                            plugin_instance_id: str) -> InstanceImportRecord | None: ...
    async def commit_record_and_checkpoint(self, *, context: TrustedRuntimeContext,
                                           expected_checkpoint: ImportCheckpoint | None,
                                           next_checkpoint: ImportCheckpoint, record: InstanceImportRecord,
                                           audit_record: RuntimeAuditRecord) -> None:
        """One trusted transaction sets RLS context, re-proves parents and exact identity,
        compares source digest/cursor, appends evidence/audit and advances cursor. Any
        failure rolls back all. Application proof readback is not a substitute for the
        adapter's transaction-local tenant/scope/definition/version/artifact EXISTS checks.
        It must enforce the exact instance composite identity across different source rows;
        duplicate instance keys are conflict/quarantine and never a second positive mapping.
        """
        ...


class InstanceImportCandidateReader(Protocol):
    async def read_page(self, *, partition: tuple[str, str], source_snapshot_id: str,
                        source_schema_version: str, source_snapshot_digest: str,
                        after_source_id: str | None, limit: int) -> Sequence[InstanceImportCandidate]: ...


@dataclass(frozen=True, slots=True)
class TrustedRuntimeContext:
    resolved_tenant_id: str | None
    subject_id: str | None
    session_id: str | None
    authenticated: bool
    correlation_id: str

    @property
    def usable(self) -> bool:
        return bool(self.authenticated and self.resolved_tenant_id and self.resolved_tenant_id.strip()
                    and self.subject_id and self.subject_id.strip() and self.session_id
                    and self.session_id.strip() and self.correlation_id.strip())


class M05IdentityEvidencePort(Protocol):
    async def resolve_installation_scope(self, *, context: TrustedRuntimeContext,
                                         anchors: ScopeAnchors) -> ScopeResolutionResult: ...
    async def resolve_plugin_instance_scope(self, *, context: TrustedRuntimeContext,
                                            identity: InstanceIdentity) -> ScopeResolutionResult: ...
    async def resolve_definition_version(self, *, context: TrustedRuntimeContext,
                                         tenant_id: str, app_id: str, manifest_version: str,
                                         expected_definition_id: str, expected_version_id: str,
                                         artifact_digest: str) -> DefinitionResolution: ...
    async def prove_subject_session(self, *, context: TrustedRuntimeContext,
                                    identity: InstanceIdentity) -> SubjectSessionBindingEvidence | None:
        """Resolve active subject and session ownership from trusted server-side records.

        The adapter must verify the subject is an active authenticated principal and
        that the exact session belongs to it in the same tenant. Caller/source values
        alone are assertions, never proof.
        """
        ...


class RedactedRuntimeAuditPort(Protocol):
    async def append(self, record: RuntimeAuditRecord) -> None: ...


async def _resolve_instance_binding(
    *, identity: InstanceIdentity, source_runtime: InstanceSourceRuntime,
    context: TrustedRuntimeContext, evidence: M05IdentityEvidencePort,
) -> tuple[InstanceBindingEvidence | None, str | None]:
    if not context.usable:
        return None, "authenticated_runtime_context_missing"
    if (identity.tenant_id != context.resolved_tenant_id or identity.subject_id != context.subject_id
        or identity.session_id != context.session_id):
        return None, "authenticated_binding_mismatch"
    if not isinstance(source_runtime.lifecycle, InstanceLifecycleState):
        return None, "unsupported_lifecycle_state"
    if not isinstance(source_runtime.availability, RuntimeAvailability):
        return None, "unsupported_runtime_availability"
    if (source_runtime.lifecycle is not InstanceLifecycleState.PUBLISHED
        or source_runtime.availability is not RuntimeAvailability.ACTIVE):
        return None, "source_runtime_not_publishable_or_active"

    installation = await evidence.resolve_installation_scope(context=context, anchors=identity.installation_scope)
    if (installation.outcome is not ScopeResolution.ACTIVE or installation.entry is None
        or installation.entry.owner_state is not OwnerState.ACTIVE
        or installation.entry.anchors != identity.installation_scope
        or installation.entry.anchors.tenant_id != identity.tenant_id
        or installation.correlation_id != context.correlation_id):
        return None, "active_installation_scope_unproven"

    instance_scope = await evidence.resolve_plugin_instance_scope(context=context, identity=identity)
    if (instance_scope.outcome is not ScopeResolution.ACTIVE or instance_scope.entry is None
        or instance_scope.entry.owner_state is not OwnerState.ACTIVE
        or instance_scope.entry.anchors.scope_kind is not ScopeKind.PLUGIN_INSTANCE
        or instance_scope.entry.anchors.scope_id != identity.plugin_instance_id
        or instance_scope.entry.anchors.plugin_instance_id != identity.plugin_instance_id
        or instance_scope.entry.anchors != identity.plugin_instance_scope
        or instance_scope.entry.anchors.tenant_id != identity.tenant_id
        or instance_scope.entry.anchors.cabinet_id != identity.installation_scope.cabinet_id
        or instance_scope.entry.anchors.project_id != identity.installation_scope.project_id
        or instance_scope.correlation_id != context.correlation_id):
        return None, "plugin_instance_registry_relation_unproven"

    definition = await evidence.resolve_definition_version(
        context=context, tenant_id=identity.tenant_id, app_id=identity.app_id,
        manifest_version=identity.manifest_version, expected_definition_id=identity.definition_id,
        expected_version_id=identity.version_id, artifact_digest=identity.artifact_digest,
    )
    ref = definition.reference
    if (definition.outcome is not DefinitionResolutionOutcome.RESOLVED or ref is None
        or ref.tenant_id != identity.tenant_id or ref.app_id != identity.app_id
        or ref.app_definition_id != identity.definition_id or ref.app_version_id != identity.version_id
        or ref.manifest_version != identity.manifest_version or ref.artifact_digest != identity.artifact_digest
        or definition.evidence.get("correlation_id") != context.correlation_id):
        return None, "definition_version_or_artifact_unproven"

    prove_binding = getattr(evidence, "prove_subject_session", None)
    if prove_binding is None:
        return None, "subject_session_evidence_port_missing"
    subject_session = await prove_binding(context=context, identity=identity)
    if subject_session is None or not subject_session.matches(identity, context):
        return None, "subject_session_binding_unproven"
    return InstanceBindingEvidence(identity, source_runtime, installation.entry,
                                   instance_scope.entry, definition, subject_session), None


@dataclass(frozen=True, slots=True)
class ImportBatch:
    records: tuple[InstanceImportRecord, ...]
    checkpoint: ImportCheckpoint
    complete: bool
    blocked_reason: str | None = None


class InstanceImportService:
    """Bounded resumable evidence import; it does not publish instances or enable M06 writes."""

    def __init__(self, reader: InstanceImportCandidateReader, store: InstanceImportStore,
                 evidence: M05IdentityEvidencePort, audit: RedactedRuntimeAuditPort,
                 *, upstream: M05UpstreamEvidence | None = None, page_size: int = 100) -> None:
        if not 1 <= page_size <= 1000:
            raise ValueError("page_size must be between 1 and 1000")
        if reader is None or store is None or evidence is None or audit is None:
            raise ValueError("M05 import requires reader, atomic store, identity evidence and audit ports")
        self._reader, self._store, self._evidence, self._audit = reader, store, evidence, audit
        self._upstream, self._page_size = upstream, page_size

    async def run_page(self, checkpoint: ImportCheckpoint, *, context: TrustedRuntimeContext) -> ImportBatch:
        if not context.correlation_id.strip():
            raise ValueError("correlation_id is required")
        reason = None
        if not context.usable:
            reason = "authenticated_runtime_context_missing"
        elif checkpoint.partition[0] != context.resolved_tenant_id:
            reason = "trusted_tenant_partition_mismatch"
        elif self._upstream is None or not self._upstream.ready_for_import(
                checkpoint.partition, checkpoint.source_snapshot_digest):
            reason = "upstream_or_m05_gate_incomplete"
        if reason:
            await self._audit.append(RuntimeAuditRecord("plugin_instance.import", "blocked", reason,
                                                        context.correlation_id))
            return ImportBatch((), checkpoint, False, reason)

        async def blocked(reason_code: str) -> ImportBatch:
            await self._audit.append(RuntimeAuditRecord(
                "plugin_instance.import", "blocked", reason_code, context.correlation_id,
            ))
            return ImportBatch((), checkpoint, False, reason_code)

        durable_cursor = await self._store.load_checkpoint(
            partition=checkpoint.partition, source_snapshot_id=checkpoint.source_snapshot_id,
            source_schema_version=checkpoint.source_schema_version,
            source_snapshot_digest=checkpoint.source_snapshot_digest)
        if durable_cursor is None:
            if checkpoint.sequence != 0 or checkpoint.after_source_id is not None:
                return await blocked("durable_checkpoint_missing")
            cursor = checkpoint
        elif (durable_cursor.partition != checkpoint.partition
              or durable_cursor.source_snapshot_id != checkpoint.source_snapshot_id
              or durable_cursor.source_schema_version != checkpoint.source_schema_version
              or durable_cursor.source_snapshot_digest != checkpoint.source_snapshot_digest):
            return await blocked("checkpoint_snapshot_mismatch")
        elif durable_cursor.sequence < 0 or (
            (durable_cursor.sequence == 0) != (durable_cursor.after_source_id is None)
        ):
            return await blocked("durable_checkpoint_invalid")
        elif checkpoint.sequence > durable_cursor.sequence:
            return await blocked("checkpoint_ahead_of_durable_cursor")
        elif (checkpoint.sequence == durable_cursor.sequence
              and checkpoint.after_source_id != durable_cursor.after_source_id):
            return await blocked("checkpoint_cursor_mismatch")
        else:
            cursor = durable_cursor
        try:
            _validate_source_cursor(cursor.after_source_id)
        except ValueError:
            return await blocked("durable_checkpoint_invalid")
        expected_cursor = durable_cursor

        rows = tuple(await self._reader.read_page(
            partition=cursor.partition, source_snapshot_id=cursor.source_snapshot_id,
            source_schema_version=cursor.source_schema_version,
            source_snapshot_digest=cursor.source_snapshot_digest,
            after_source_id=cursor.after_source_id, limit=self._page_size))
        for candidate in rows:
            _validate_source_identity(candidate.source_kind, candidate.source_id, candidate.schema_version)
        if len(rows) > self._page_size or len({
            (r.source_kind, r.source_id, r.schema_version) for r in rows
        }) != len(rows):
            raise ValueError("candidate page exceeds limit or contains duplicate source identities")
        ordered = tuple(sorted(rows, key=lambda item: item.source_id))
        if any(cursor.after_source_id is not None and row.source_id <= cursor.after_source_id for row in ordered):
            raise ValueError("candidate page is not strictly after the durable checkpoint")

        result: list[InstanceImportRecord] = []
        seen_instance_ids: set[str] = set()
        seen_source_keys: set[tuple[str, str, str]] = set()
        for candidate in ordered:
            if candidate.schema_version != cursor.source_schema_version:
                raise ValueError("candidate schema version differs from the pinned source snapshot")
            if candidate.source_kind == "" or candidate.source_id == "":
                raise ValueError("candidate source identity is empty")
            next_checkpoint = cursor.advance(candidate.source_id)
            if (candidate.source_kind, candidate.source_id, candidate.schema_version) in seen_source_keys:
                raise ValueError("candidate page contains duplicate source identity")
            seen_source_keys.add((candidate.source_kind, candidate.source_id, candidate.schema_version))
            prior = await self._store.find(partition=cursor.partition, source_kind=candidate.source_kind,
                                           source_id=candidate.source_id,
                                           schema_version=candidate.schema_version)
            binding = None
            row_reason = None
            outcome = ImportOutcome.IMPORTED
            mapping_checkpoint = next_checkpoint
            if prior and prior.source_digest == candidate.digest:
                if prior.binding_evidence is None:
                    outcome = prior.outcome
                    row_reason = prior.reason_code
                elif (candidate.identity is None or candidate.source_runtime is None
                      or candidate.identity != prior.binding_evidence.identity):
                    outcome, row_reason = ImportOutcome.BLOCKED, "replay_exact_binding_unavailable"
                else:
                    _, replay_reason = await _resolve_instance_binding(
                        identity=candidate.identity, source_runtime=candidate.source_runtime,
                        context=context, evidence=self._evidence,
                    )
                    if replay_reason is not None:
                        outcome, row_reason = ImportOutcome.BLOCKED, replay_reason
                    else:
                        outcome = ImportOutcome.REPLAY
                        binding = prior.binding_evidence
                        mapping_checkpoint = prior.checkpoint
            elif prior:
                outcome, row_reason = ImportOutcome.CONFLICT, "source_digest_changed"
            elif (candidate.identity is not None
                  and candidate.identity.plugin_instance_id in seen_instance_ids):
                outcome, row_reason = ImportOutcome.QUARANTINED, "duplicate_plugin_instance_identity"
            elif candidate.identity is None or candidate.source_runtime is None:
                outcome, row_reason = ImportOutcome.QUARANTINED, "instance_identity_or_generation_missing"
            elif not isinstance(candidate.source_runtime.lifecycle, InstanceLifecycleState):
                outcome, row_reason = ImportOutcome.QUARANTINED, "unsupported_lifecycle_state"
            elif not isinstance(candidate.source_runtime.availability, RuntimeAvailability):
                outcome, row_reason = ImportOutcome.QUARANTINED, "unsupported_runtime_availability"
            elif candidate.source_runtime.lifecycle is InstanceLifecycleState.QUARANTINED:
                outcome, row_reason = ImportOutcome.QUARANTINED, "source_runtime_already_quarantined"
            elif (candidate.source_runtime.lifecycle is not InstanceLifecycleState.PUBLISHED
                  or candidate.source_runtime.availability is not RuntimeAvailability.ACTIVE):
                outcome, row_reason = ImportOutcome.QUARANTINED, "source_runtime_not_publishable_or_active"
            elif candidate.identity.partition != cursor.partition:
                outcome, row_reason = ImportOutcome.QUARANTINED, "instance_partition_mismatch"
            else:
                binding, row_reason = await _resolve_instance_binding(
                    identity=candidate.identity, source_runtime=candidate.source_runtime,
                    context=context, evidence=self._evidence,
                )
                if row_reason is not None:
                    outcome = ImportOutcome.QUARANTINED
                else:
                    existing_instance = await self._store.find_instance(
                        partition=cursor.partition,
                        plugin_instance_id=candidate.identity.plugin_instance_id,
                    )
                    if existing_instance is not None and (
                        existing_instance.source_kind,
                        existing_instance.source_id,
                        existing_instance.schema_version,
                    ) != (candidate.source_kind, candidate.source_id, candidate.schema_version):
                        outcome, row_reason, binding = (
                            ImportOutcome.QUARANTINED, "duplicate_plugin_instance_identity", None)
            if outcome is ImportOutcome.IMPORTED and candidate.identity is not None:
                seen_instance_ids.add(candidate.identity.plugin_instance_id)
            if outcome in {ImportOutcome.QUARANTINED, ImportOutcome.CONFLICT, ImportOutcome.BLOCKED}:
                binding = None
            record = InstanceImportRecord(candidate.source_kind, candidate.source_id, candidate.schema_version,
                                          candidate.digest, mapping_checkpoint, outcome, row_reason,
                                          context.correlation_id, binding, next_checkpoint)
            audit = RuntimeAuditRecord("plugin_instance.import", outcome.value, row_reason or outcome.value,
                                       context.correlation_id,
                                       candidate.source_runtime.runtime_generation if candidate.source_runtime else None,
                                       candidate.source_runtime.writer_generation if candidate.source_runtime else None)
            await self._store.commit_record_and_checkpoint(context=context, expected_checkpoint=expected_cursor,
                                                           next_checkpoint=next_checkpoint,
                                                           record=record, audit_record=audit)
            expected_cursor = next_checkpoint
            result.append(record)
            cursor = next_checkpoint
        return ImportBatch(tuple(result), cursor, len(rows) < self._page_size)


class QuarantineRetryOutcome(StrEnum):
    RETRIED = "retried"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class QuarantineRetryResult:
    outcome: QuarantineRetryOutcome
    attempt: QuarantineRetryAttempt | None
    blocked_reason: str | None = None


class InstanceQuarantineRetryService:
    """Retry one exact quarantine without replacing its immutable source evidence."""

    def __init__(self, candidates: InstanceExactCandidatePort, store: InstanceQuarantineRetryPort,
                 evidence: M05IdentityEvidencePort, audit: RedactedRuntimeAuditPort, *,
                 upstream: M05UpstreamEvidence | None) -> None:
        if candidates is None or store is None or evidence is None or audit is None:
            raise ValueError("quarantine retry requires exact candidate, persistence, identity and audit ports")
        self._candidates, self._store, self._evidence, self._audit = candidates, store, evidence, audit
        self._upstream = upstream

    async def retry(self, *, partition: tuple[str, str], source_snapshot_id: str,
                    source_schema_version: str, source_snapshot_digest: str,
                    source_kind: str, source_id: str, schema_version: str,
                    context: TrustedRuntimeContext) -> QuarantineRetryResult:
        async def blocked(reason: str) -> QuarantineRetryResult:
            await self._audit.append(RuntimeAuditRecord(
                "plugin_instance.quarantine_retry", "blocked", reason,
                context.correlation_id or "missing-correlation",
            ))
            return QuarantineRetryResult(QuarantineRetryOutcome.BLOCKED, None, reason)

        if not context.usable:
            return await blocked("authenticated_runtime_context_missing")
        if (partition[0] != context.resolved_tenant_id or len(partition) != 2
            or any(not value.strip() for value in partition)):
            return await blocked("trusted_tenant_partition_mismatch")
        if (not _valid_digest(source_snapshot_digest) or not source_snapshot_id.strip()
            or source_schema_version != schema_version):
            return await blocked("source_identity_invalid")
        try:
            _validate_source_identity(source_kind, source_id, schema_version)
        except ValueError:
            return await blocked("source_identity_invalid")
        if self._upstream is None or not self._upstream.ready_for_import(partition, source_snapshot_digest):
            return await blocked("upstream_or_m05_gate_incomplete")

        original = await self._store.find_quarantine(
            partition=partition, source_kind=source_kind, source_id=source_id,
            schema_version=schema_version)
        if (original is None or original.checkpoint.partition != partition
            or original.outcome is not ImportOutcome.QUARANTINED):
            return await blocked("quarantine_record_missing_or_not_retryable")
        if (original.cursor_checkpoint is None
            or original.cursor_checkpoint.source_snapshot_id != source_snapshot_id
            or original.cursor_checkpoint.source_schema_version != source_schema_version
            or original.cursor_checkpoint.source_snapshot_digest != source_snapshot_digest):
            return await blocked("quarantine_snapshot_mismatch")

        state = await self._store.load_retry_state(
            partition=partition, source_kind=source_kind, source_id=source_id,
            schema_version=schema_version)
        try:
            _validate_retry_state_for_source(
                state, source_kind=source_kind, source_id=source_id,
                schema_version=schema_version, source_digest=original.source_digest,
                checkpoint=original.checkpoint,
            )
        except ValueError:
            return await blocked("quarantine_retry_history_invalid")
        latest = state.latest
        if latest is not None and latest.outcome is ImportOutcome.IMPORTED:
            return await blocked("quarantine_already_resolved")
        candidate = await self._candidates.read_exact(
            partition=partition, source_snapshot_id=source_snapshot_id,
            source_schema_version=source_schema_version,
            source_snapshot_digest=source_snapshot_digest, source_kind=source_kind,
            source_id=source_id, schema_version=schema_version)
        number = len(state.attempts) + 1
        binding = None
        reason = None
        outcome = ImportOutcome.QUARANTINED
        if candidate is None:
            reason = "source_row_missing"
        elif ((candidate.source_kind, candidate.source_id, candidate.schema_version)
              != (source_kind, source_id, schema_version)):
            reason = "source_identity_mismatch"
        else:
            assert candidate is not None
            candidate_identity = candidate.identity
            candidate_runtime = candidate.source_runtime
            try:
                _validate_source_identity(candidate.source_kind, candidate.source_id, candidate.schema_version)
            except ValueError:
                reason = "source_identity_invalid"
            if reason is None:
                if candidate.digest != original.source_digest:
                    outcome, reason = ImportOutcome.CONFLICT, "source_digest_changed"
                elif candidate_identity is None or candidate_runtime is None:
                    reason = "instance_identity_or_generation_missing"
                elif candidate_identity.partition != partition:
                    reason = "instance_partition_mismatch"
                else:
                    binding, reason = await _resolve_instance_binding(
                        identity=candidate_identity, source_runtime=candidate_runtime,
                        context=context, evidence=self._evidence)
                    if reason is None:
                        existing = await self._store.find_instance(
                            partition=partition, plugin_instance_id=candidate_identity.plugin_instance_id)
                        if existing is not None and (
                            existing.source_kind, existing.source_id, existing.schema_version
                        ) != (source_kind, source_id, schema_version):
                            binding, reason = None, "duplicate_plugin_instance_identity"
                    if reason is None:
                        outcome = ImportOutcome.IMPORTED
        attempt = QuarantineRetryAttempt(
            source_kind=source_kind, source_id=source_id, schema_version=schema_version,
            source_digest=original.source_digest, attempt_number=number, outcome=outcome,
            reason_code=reason, correlation_id=context.correlation_id,
            checkpoint=original.checkpoint, binding_evidence=binding,
        )
        persisted = await self._store.commit_retry_attempt(
            context=context, original=original, expected_attempt_count=len(state.attempts),
            attempt=attempt,
            audit_record=RuntimeAuditRecord(
                "plugin_instance.quarantine_retry", outcome.value, reason or outcome.value,
                context.correlation_id,
                candidate.source_runtime.runtime_generation if candidate and candidate.source_runtime else None,
                candidate.source_runtime.writer_generation if candidate and candidate.source_runtime else None,
            ),
        )
        if persisted != attempt:
            return QuarantineRetryResult(QuarantineRetryOutcome.BLOCKED, None,
                                         "retry_persistence_result_mismatch")
        return QuarantineRetryResult(QuarantineRetryOutcome.RETRIED, persisted)


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    identity: InstanceIdentity
    lifecycle: InstanceLifecycleState
    availability: RuntimeAvailability
    runtime_generation: int
    writer_generation: int

    def __post_init__(self) -> None:
        if min(self.runtime_generation, self.writer_generation) < 0:
            raise ValueError("runtime generations must be non-negative")


@dataclass(frozen=True, slots=True)
class InstanceResolveQuery:
    identity: InstanceIdentity
    runtime_generation: int
    writer_generation: int

    def __post_init__(self) -> None:
        if min(self.runtime_generation, self.writer_generation) < 0:
            raise ValueError("expected generations must be non-negative")


@dataclass(frozen=True, slots=True)
class WriterCasResult:
    changed: bool
    snapshot: RuntimeSnapshot | None
    reason_code: str


class RuntimeInstanceLifecycle(Protocol):
    async def resolve_exact(self, *, query: InstanceResolveQuery,
                            context: TrustedRuntimeContext) -> RuntimeSnapshot | None: ...
    async def compare_and_set_writer(self, *, identity: InstanceIdentity, context: TrustedRuntimeContext,
                                     expected_runtime_generation: int, expected_writer_generation: int,
                                     next_writer_generation: int, audit_record: RuntimeAuditRecord) -> WriterCasResult:
        """In one transaction, re-prove M04/M04E/registry identity, CAS the complete
        identity/generation tuple and atomically append success-or-denial audit.

        Adapter must convert ``audit_record.outcome`` to ``stale-generation``/``deny``
        when the affected-row count is zero. It may never persist an ``advanced`` audit
        for a failed CAS or return a positive result when audit append fails. A malformed
        returned row must roll back rather than commit a mutation for the caller to deny.
        """
        ...
    async def compare_and_set_runtime(self, *, identity: InstanceIdentity, context: TrustedRuntimeContext,
                                      expected_runtime_generation: int, expected_writer_generation: int,
                                      next_runtime_generation: int, next_writer_generation: int,
                                      audit_record: RuntimeAuditRecord) -> WriterCasResult:
        """Re-prove full M04/M04E identity, CAS both epochs and audit atomically; a bad
        returned row must roll back inside the adapter rather than mutate then deny."""
        ...
    async def compare_and_set_lifecycle(self, *, identity: InstanceIdentity, context: TrustedRuntimeContext,
                                        expected_lifecycle: InstanceLifecycleState,
                                        next_lifecycle: InstanceLifecycleState,
                                        expected_runtime_generation: int, expected_writer_generation: int,
                                        next_writer_generation: int,
                                        audit_record: RuntimeAuditRecord) -> WriterCasResult:
        """Re-prove full identity, CAS lifecycle and both expected epochs, and audit
        atomically; invalid returned state must roll back inside the adapter.
        """
        ...


class ResolvePluginInstancePort(Protocol):
    async def resolve(self, *, context: TrustedRuntimeContext,
                      query: InstanceResolveQuery) -> RuntimeSnapshot | None: ...


class FailClosedResolvePluginInstance:
    """Resolve only an exact active instance with matching authenticated subject/session."""

    def __init__(self, delegate: RuntimeInstanceLifecycle, audit: RedactedRuntimeAuditPort,
                 *, upstream: M05UpstreamEvidence | None = None,
                 identity_evidence: M05IdentityEvidencePort | None = None) -> None:
        if delegate is None or audit is None:
            raise ValueError("exact runtime lifecycle and redacted audit ports are mandatory")
        self._delegate, self._audit, self._upstream = delegate, audit, upstream
        self._identity_evidence = identity_evidence

    async def resolve(self, *, context: TrustedRuntimeContext,
                      query: InstanceResolveQuery) -> RuntimeSnapshot | None:
        identity = query.identity
        reason = None
        if not context.usable:
            reason = "authenticated_runtime_context_missing"
        elif (identity.tenant_id != context.resolved_tenant_id or identity.subject_id != context.subject_id
              or identity.session_id != context.session_id):
            reason = "authenticated_binding_mismatch"
        elif self._upstream is None or not self._upstream.ready_for_positive_transition(identity.partition):
            reason = "upstream_or_m05_gate_incomplete"
        elif self._identity_evidence is None:
            reason = "m04_m04e_identity_evidence_port_missing"
        snapshot = None
        if reason is None:
            reason = await _prove_runtime_identity(identity, context, self._identity_evidence)
        if reason is None:
            snapshot = await self._delegate.resolve_exact(query=query, context=context)
            if snapshot is None:
                reason = "instance_missing_or_not_unique"
            elif snapshot.identity != identity:
                reason = "exact_instance_binding_mismatch"
            elif snapshot.runtime_generation != query.runtime_generation:
                reason = "stale_runtime_generation"
            elif snapshot.writer_generation != query.writer_generation:
                reason = "stale_writer_generation"
            elif snapshot.lifecycle is not InstanceLifecycleState.PUBLISHED:
                reason = "instance_lifecycle_not_published"
            elif snapshot.availability is not RuntimeAvailability.ACTIVE:
                reason = "runtime_not_active"
        allowed = reason is None and snapshot is not None
        await self._audit.append(RuntimeAuditRecord("plugin_instance.resolve",
            "allowed" if allowed else "denied", "exact_instance_resolved" if allowed else str(reason),
            context.correlation_id or "missing-correlation", query.runtime_generation, query.writer_generation))
        return snapshot if allowed else None


def _audit(event: str, outcome: str, reason: str, correlation_id: str,
           runtime_generation: int, writer_generation: int) -> RuntimeAuditRecord:
    # Audit deliberately omits raw identity, tenant, scope, subject, session and arguments.
    return RuntimeAuditRecord(event, outcome, reason, correlation_id, runtime_generation, writer_generation)


async def _prove_runtime_identity(identity: InstanceIdentity, context: TrustedRuntimeContext,
                                  evidence: M05IdentityEvidencePort | None) -> str | None:
    if evidence is None:
        return "m04_m04e_identity_evidence_port_missing"
    if (identity.tenant_id != context.resolved_tenant_id or identity.subject_id != context.subject_id
        or identity.session_id != context.session_id):
        return "authenticated_binding_mismatch"
    if getattr(evidence, "prove_subject_session", None) is None:
        return "subject_session_evidence_port_missing"
    _, reason = await _resolve_instance_binding(
        identity=identity,
        source_runtime=InstanceSourceRuntime(InstanceLifecycleState.PUBLISHED, 0, 0),
        context=context, evidence=evidence,
    )
    return reason


async def advance_writer_generation(lifecycle: RuntimeInstanceLifecycle, *, identity: InstanceIdentity,
                                    context: TrustedRuntimeContext,
                                    expected_runtime_generation: int, expected_writer_generation: int,
                                    upstream: M05UpstreamEvidence | None,
                                    correlation_id: str,
                                    identity_evidence: M05IdentityEvidencePort | None = None) -> WriterCasResult:
    if not context.usable or (context.resolved_tenant_id, context.subject_id, context.session_id) != (
        identity.tenant_id, identity.subject_id, identity.session_id
    ):
        return WriterCasResult(False, None, "authenticated_binding_mismatch")
    if min(expected_runtime_generation, expected_writer_generation) < 0:
        return WriterCasResult(False, None, "invalid_generation")
    if not correlation_id.strip() or correlation_id != context.correlation_id:
        return WriterCasResult(False, None, "missing_correlation_id")
    if upstream is None or not upstream.ready_for_positive_transition(identity.partition):
        return WriterCasResult(False, None, "upstream_or_m05_gate_incomplete")
    proof_reason = await _prove_runtime_identity(identity, context, identity_evidence)
    if proof_reason:
        return WriterCasResult(False, None, proof_reason)
    result = await lifecycle.compare_and_set_writer(
        identity=identity, context=context, expected_runtime_generation=expected_runtime_generation,
        expected_writer_generation=expected_writer_generation,
        next_writer_generation=expected_writer_generation + 1,
        audit_record=_audit("plugin_instance.writer_generation", "advanced", "writer_generation_cas",
                            correlation_id, expected_runtime_generation, expected_writer_generation + 1))
    if not result.changed or result.snapshot is None:
        return WriterCasResult(False, None, result.reason_code or "writer_cas_conflict")
    snap = result.snapshot
    if (snap.identity != identity or snap.lifecycle is not InstanceLifecycleState.PUBLISHED
        or snap.availability is not RuntimeAvailability.ACTIVE
        or snap.runtime_generation != expected_runtime_generation
        or snap.writer_generation != expected_writer_generation + 1):
        # The persistence port must roll back a malformed row before returning it.
        return WriterCasResult(False, None, "stale_worker_denied")
    return result


async def advance_runtime_generation(lifecycle: RuntimeInstanceLifecycle, *, identity: InstanceIdentity,
                                     context: TrustedRuntimeContext,
                                     expected_runtime_generation: int, expected_writer_generation: int,
                                     upstream: M05UpstreamEvidence | None,
                                     correlation_id: str,
                                     identity_evidence: M05IdentityEvidencePort | None = None) -> WriterCasResult:
    if not context.usable or (context.resolved_tenant_id, context.subject_id, context.session_id) != (
        identity.tenant_id, identity.subject_id, identity.session_id
    ):
        return WriterCasResult(False, None, "authenticated_binding_mismatch")
    if min(expected_runtime_generation, expected_writer_generation) < 0:
        return WriterCasResult(False, None, "invalid_generation")
    if not correlation_id.strip() or correlation_id != context.correlation_id:
        return WriterCasResult(False, None, "missing_correlation_id")
    if upstream is None or not upstream.ready_for_positive_transition(identity.partition):
        return WriterCasResult(False, None, "upstream_or_m05_gate_incomplete")
    proof_reason = await _prove_runtime_identity(identity, context, identity_evidence)
    if proof_reason:
        return WriterCasResult(False, None, proof_reason)
    result = await lifecycle.compare_and_set_runtime(
        identity=identity, context=context,
        expected_runtime_generation=expected_runtime_generation,
        expected_writer_generation=expected_writer_generation,
        next_runtime_generation=expected_runtime_generation + 1,
        next_writer_generation=expected_writer_generation + 1,
        audit_record=_audit("plugin_instance.runtime_generation", "advanced", "runtime_generation_cas",
                            correlation_id, expected_runtime_generation + 1,
                            expected_writer_generation + 1))
    if not result.changed or result.snapshot is None:
        return WriterCasResult(False, None, result.reason_code or "runtime_cas_conflict")
    snap = result.snapshot
    if (snap.identity != identity or snap.lifecycle is not InstanceLifecycleState.PUBLISHED
        or snap.availability is not RuntimeAvailability.ACTIVE
        or snap.runtime_generation != expected_runtime_generation + 1
        or snap.writer_generation != expected_writer_generation + 1):
        # The adapter must roll back the CAS before returning any malformed result.
        return WriterCasResult(False, None, "stale_runtime_denied")
    return result


_ALLOWED_TRANSITIONS = {
    InstanceLifecycleState.DRAFT: frozenset({InstanceLifecycleState.PUBLISHED, InstanceLifecycleState.DISABLED,
                                              InstanceLifecycleState.REVOKED, InstanceLifecycleState.QUARANTINED}),
    InstanceLifecycleState.PUBLISHED: frozenset({InstanceLifecycleState.DISABLED, InstanceLifecycleState.REVOKED,
                                                  InstanceLifecycleState.QUARANTINED}),
    InstanceLifecycleState.DISABLED: frozenset({InstanceLifecycleState.PUBLISHED, InstanceLifecycleState.REVOKED,
                                                 InstanceLifecycleState.QUARANTINED}),
    InstanceLifecycleState.REVOKED: frozenset(),
    InstanceLifecycleState.QUARANTINED: frozenset(),
}


class InstanceLifecycleService:
    def __init__(self, lifecycle: RuntimeInstanceLifecycle, *,
                 upstream: M05UpstreamEvidence | None, correlation_id: str,
                 identity_evidence: M05IdentityEvidencePort | None = None) -> None:
        if lifecycle is None or not correlation_id.strip():
            raise ValueError("runtime lifecycle port and correlation ID are mandatory")
        self._lifecycle, self._upstream, self._correlation_id = lifecycle, upstream, correlation_id
        self._identity_evidence = identity_evidence

    async def transition(self, *, identity: InstanceIdentity, expected_lifecycle: InstanceLifecycleState,
                         next_lifecycle: InstanceLifecycleState, context: TrustedRuntimeContext,
                         expected_runtime_generation: int, expected_writer_generation: int) -> WriterCasResult:
        if not context.usable or (context.resolved_tenant_id, context.subject_id, context.session_id) != (
            identity.tenant_id, identity.subject_id, identity.session_id
        ):
            return WriterCasResult(False, None, "authenticated_binding_mismatch")
        if not self._correlation_id.strip() or self._correlation_id != context.correlation_id:
            return WriterCasResult(False, None, "invalid_context_or_generation")
        if min(expected_runtime_generation, expected_writer_generation) < 0:
            return WriterCasResult(False, None, "invalid_context_or_generation")
        if not isinstance(expected_lifecycle, InstanceLifecycleState) or not isinstance(next_lifecycle, InstanceLifecycleState):
            return WriterCasResult(False, None, "unsupported_lifecycle_state")
        if next_lifecycle not in _ALLOWED_TRANSITIONS[expected_lifecycle]:
            return WriterCasResult(False, None, "lifecycle_transition_not_allowed")
        if next_lifecycle is InstanceLifecycleState.PUBLISHED:
            if (self._upstream is None
                or not self._upstream.ready_for_positive_transition(identity.partition)):
                return WriterCasResult(False, None, "upstream_or_m05_gate_incomplete")
        proof_reason = await _prove_runtime_identity(identity, context, self._identity_evidence)
        if proof_reason:
            return WriterCasResult(False, None, proof_reason)
        next_writer = expected_writer_generation + 1
        result = await self._lifecycle.compare_and_set_lifecycle(
            identity=identity, context=context, expected_lifecycle=expected_lifecycle,
            next_lifecycle=next_lifecycle,
            expected_runtime_generation=expected_runtime_generation,
            expected_writer_generation=expected_writer_generation, next_writer_generation=next_writer,
            audit_record=_audit("plugin_instance.lifecycle", next_lifecycle.value, "lifecycle_generation_cas",
                                self._correlation_id, expected_runtime_generation, next_writer))
        if not result.changed or result.snapshot is None:
            return WriterCasResult(False, None, result.reason_code or "lifecycle_cas_conflict")
        snap = result.snapshot
        if (snap.identity != identity or snap.lifecycle is not next_lifecycle
            or snap.runtime_generation != expected_runtime_generation or snap.writer_generation != next_writer):
            return WriterCasResult(False, None, "lifecycle_cas_result_mismatch")
        return result


@dataclass(frozen=True, slots=True)
class FinalDeltaEvidence:
    partition: tuple[str, str]
    source_snapshot_id: str
    source_snapshot_digest: str
    checkpoint: ImportCheckpoint
    source_count: int
    evidence_digest: str

    @property
    def ready(self) -> bool:
        return (self.partition == self.checkpoint.partition and self.source_count >= 0
                and self.source_snapshot_id == self.checkpoint.source_snapshot_id
                and self.source_snapshot_digest == self.checkpoint.source_snapshot_digest
                and self.checkpoint.sequence == self.source_count
                and _valid_digest(self.evidence_digest))


@dataclass(frozen=True, slots=True)
class ParityEvidence:
    partition: tuple[str, str]
    checkpoint_digest: str
    expected_source_count: int
    imported_source_count: int
    unknown_mappings: int
    unresolved_bindings: int
    mismatch_count: int
    evidence_digest: str

    @property
    def ready(self) -> bool:
        return (len(self.partition) == 2 and all(value.strip() for value in self.partition)
                and self.expected_source_count >= 0 and self.imported_source_count >= 0
                and self.imported_source_count == self.expected_source_count
                and min(self.unknown_mappings, self.unresolved_bindings, self.mismatch_count) == 0
                and _valid_digest(self.checkpoint_digest) and _valid_digest(self.evidence_digest))


@dataclass(frozen=True, slots=True)
class LegacyWriterFenceEvidence:
    partition: tuple[str, str]
    writer_generation: int
    active_workers_remaining: int
    fence_digest: str

    @property
    def ready(self) -> bool:
        return (len(self.partition) == 2 and all(value.strip() for value in self.partition)
                and self.writer_generation >= 0 and self.active_workers_remaining == 0
                and _valid_digest(self.fence_digest))


@dataclass(frozen=True, slots=True)
class PromotionGateEvidence:
    name: str
    passed: bool
    evidence_digest: str | None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class PromotionRecord:
    record_id: str
    task_id: str
    status: HandoffStatus
    partition: tuple[str, str]
    source_snapshot_digest: str
    canonical_digest: str
    checkpoint: ImportCheckpoint
    imported_count: int
    replay_count: int
    quarantined_count: int
    conflict_count: int
    gate_evidence: tuple[PromotionGateEvidence, ...]
    actor: str
    recorded_at: str
    decision_owner: str
    writer_generation: int | None
    correlation_id: str
    m06_handoff: HandoffStatus
    legacy_read_write_owner: str = "legacy"
    canonical_positive_writes: bool = False
    retry_evidence_digest: str | None = None
    final_delta_checkpoint_digest: str | None = None
    parity_evidence_digest: str | None = None
    legacy_writer_fenced: bool = False
    unknown_mappings: int = 0
    unresolved_positive_bindings: int = 0


class M05PromotionRecordPort(Protocol):
    async def append_immutable(self, record: PromotionRecord) -> None: ...


def _records_with_latest_retries(
    records: Sequence[InstanceImportRecord], retry_attempts: Sequence[QuarantineRetryAttempt],
) -> tuple[InstanceImportRecord, ...]:
    for row in records:
        _validate_source_identity(row.source_kind, row.source_id, row.schema_version)
    for attempt in retry_attempts:
        _validate_source_identity(attempt.source_kind, attempt.source_id, attempt.schema_version)
    by_key = {(row.source_kind, row.source_id, row.schema_version): row for row in records}
    latest: dict[tuple[str, str, str], QuarantineRetryAttempt] = {}
    histories: dict[tuple[str, str, str], list[QuarantineRetryAttempt]] = {}
    for attempt in retry_attempts:
        key = (attempt.source_kind, attempt.source_id, attempt.schema_version)
        original = by_key.get(key)
        if (original is None or original.outcome is not ImportOutcome.QUARANTINED
            or original.source_digest != attempt.source_digest
            or original.checkpoint != attempt.checkpoint):
            raise ValueError("retry evidence must match an exact original quarantine")
        history = histories.setdefault(key, [])
        if any(prior.attempt_number == attempt.attempt_number for prior in history):
            raise ValueError("retry attempt numbers cannot be duplicated")
        history.append(attempt)
        latest[key] = attempt
    for history in histories.values():
        history.sort(key=lambda attempt: attempt.attempt_number)
        if [attempt.attempt_number for attempt in history] != list(range(1, len(history) + 1)):
            raise ValueError("retry history must be contiguous and monotonic")
    resolved: list[InstanceImportRecord] = []
    for key, original in by_key.items():
        attempt = latest.get(key)
        if attempt is None:
            resolved.append(original)
            continue
        if attempt.attempt_number <= 0:
            raise ValueError("retry attempt number must be positive")
        if attempt.outcome is ImportOutcome.IMPORTED:
            if attempt.binding_evidence is None:
                raise ValueError("successful retry lacks binding evidence")
            resolved.append(replace(original, outcome=ImportOutcome.IMPORTED,
                                    reason_code=None, binding_evidence=attempt.binding_evidence))
        else:
            resolved.append(replace(original, outcome=attempt.outcome,
                                    reason_code=attempt.reason_code, binding_evidence=None))
    if len(by_key) != len(records):
        raise ValueError("handoff records contain duplicate source identities")
    return tuple(resolved)


def build_m06_handoff_record(*, record_id: str, records: Sequence[InstanceImportRecord],
                             checkpoint: ImportCheckpoint, upstream: M05UpstreamEvidence | None,
                             actor: str, recorded_at: str, decision_owner: str,
                             writer_generation: int | None, correlation_id: str,
                             final_delta: FinalDeltaEvidence | None,
                             parity: ParityEvidence | None,
                             legacy_fence: LegacyWriterFenceEvidence | None,
                             unknown_mappings: int = 0,
                             unresolved_positive_bindings: int = 0,
                             retry_attempts: Sequence[QuarantineRetryAttempt] = ()) -> PromotionRecord:
    if not all(x.strip() for x in (record_id, actor, recorded_at, decision_owner, correlation_id)):
        raise ValueError("promotion identity and decision metadata are required")
    if writer_generation is not None and writer_generation < 0:
        raise ValueError("writer generation must be non-negative")
    if min(unknown_mappings, unresolved_positive_bindings) < 0:
        raise ValueError("promotion blocker counts must be non-negative")
    _validate_source_cursor(checkpoint.after_source_id)
    records = _records_with_latest_retries(records, retry_attempts)
    partition = checkpoint.partition
    imported = tuple(r for r in records if r.outcome is ImportOutcome.IMPORTED)
    replayed = tuple(r for r in records if r.outcome is ImportOutcome.REPLAY)
    bad = tuple(r for r in records if r.outcome in {ImportOutcome.QUARANTINED, ImportOutcome.BLOCKED})
    conflicts = tuple(r for r in records if r.outcome is ImportOutcome.CONFLICT)
    accepted = imported + replayed
    duplicate = len({(r.source_kind, r.source_id, r.schema_version) for r in records}) != len(records)

    def binding_valid(row: InstanceImportRecord) -> bool:
        evidence = row.binding_evidence
        if evidence is None:
            return False
        identity = evidence.identity
        source_runtime = evidence.source_runtime
        installation = evidence.installation_scope
        plugin_scope = evidence.plugin_instance_scope
        definition = evidence.definition_version.reference
        cursor = row.cursor_checkpoint
        subject_session = evidence.subject_session
        return (row.checkpoint.partition == partition and cursor is not None
                and cursor.partition == partition
                and cursor.source_snapshot_id == checkpoint.source_snapshot_id
                and cursor.source_schema_version == checkpoint.source_schema_version
                and cursor.source_snapshot_digest == checkpoint.source_snapshot_digest
                and cursor.sequence <= checkpoint.sequence
                and identity.partition == partition
                and identity.scope_id == identity.plugin_instance_id
                and identity.plugin_instance_scope == plugin_scope.anchors
                and identity.installation_scope == installation.anchors
                and installation.owner_state is OwnerState.ACTIVE
                and plugin_scope.owner_state is OwnerState.ACTIVE
                and source_runtime.lifecycle is InstanceLifecycleState.PUBLISHED
                and source_runtime.availability is RuntimeAvailability.ACTIVE
                and subject_session is not None
                and subject_session.matches_identity(identity, correlation_id=row.correlation_id)
                and definition is not None
                and definition.tenant_id == identity.tenant_id
                and definition.app_id == identity.app_id
                and definition.app_definition_id == identity.definition_id
                and definition.app_version_id == identity.version_id
                and definition.manifest_version == identity.manifest_version
                and definition.artifact_digest == identity.artifact_digest)

    malformed = not all(binding_valid(row) for row in accepted)
    accepted_instance_ids = [row.binding_evidence.identity.plugin_instance_id
                             for row in accepted if row.binding_evidence is not None]
    duplicate_instance = len(accepted_instance_ids) != len(set(accepted_instance_ids))
    gates: list[PromotionGateEvidence] = []
    if upstream is None:
        gates.extend(PromotionGateEvidence(n, False, None, "upstream_evidence_missing")
                     for n in ("M04_scope", "M04E_definition_version", "M05_runtime"))
    else:
        scope, export, runtime = upstream.m04_scope, upstream.m04e_export, upstream.m05_runtime
        scope_ready = scope.ready and scope.partition == partition
        export_ready = (export.status is M04EHandoffStatus.READY and export.tenant_id == partition[0]
                        and export.app_definition_id == partition[1]
                        and _valid_digest(export.canonical_digest))
        runtime_ready = runtime.ready and runtime.partition == partition
        gates.extend((
            PromotionGateEvidence("M04_scope", scope_ready, scope.canonical_digest,
                                  None if scope_ready else "m04_scope_gate_incomplete"),
            PromotionGateEvidence("M04E_definition_version", export_ready, export.canonical_digest,
                                  None if export_ready else "m04e_handoff_blocked"),
            PromotionGateEvidence("M05_runtime", runtime_ready, runtime.canonical_digest,
                                  None if runtime_ready else "m05_runtime_gate_incomplete"),
        ))
    retry_digest = _digest(tuple(sorted(
        (attempt.source_kind, attempt.source_id, attempt.schema_version, attempt.source_digest,
         attempt.attempt_number, attempt.outcome.value, attempt.reason_code,
         attempt.checkpoint.digest, attempt.correlation_id,
         attempt.binding_evidence.identity.digest if attempt.binding_evidence else None)
        for attempt in retry_attempts
    )))
    rows_digest = _digest({
        "accepted_rows": tuple(sorted(
            f"{r.source_kind}:{r.source_id}:{r.source_digest}:{r.binding_evidence.identity.digest}"
            for r in accepted if r.binding_evidence is not None)),
        "retry_evidence_digest": retry_digest,
    })
    delta_ready = (final_delta is not None and final_delta.ready
                   and final_delta.partition == partition
                   and final_delta.checkpoint == checkpoint)
    parity_ready = (parity is not None and parity.ready
                    and parity.partition == partition
                    and parity.checkpoint_digest == checkpoint.digest
                    and final_delta is not None
                    and parity.expected_source_count == final_delta.source_count
                    and parity.imported_source_count == len(accepted)
                    and parity.unknown_mappings == unknown_mappings
                    and parity.unresolved_bindings == unresolved_positive_bindings)
    fence_ready = (legacy_fence is not None and legacy_fence.ready
                   and legacy_fence.partition == partition
                   and legacy_fence.writer_generation == writer_generation)
    expected_source_count = final_delta.source_count if final_delta is not None else -1
    imports_ready = (delta_ready and parity_ready and fence_ready
        and unknown_mappings == 0 and unresolved_positive_bindings == 0
        and expected_source_count > 0 and len(records) == expected_source_count
        and len(accepted) == expected_source_count and not bad and not conflicts
        and not malformed and not duplicate and not duplicate_instance
        and all(r.cursor_checkpoint is not None
                and r.cursor_checkpoint.source_snapshot_id == checkpoint.source_snapshot_id
                and r.cursor_checkpoint.source_schema_version == checkpoint.source_schema_version
                and r.cursor_checkpoint.source_snapshot_digest == checkpoint.source_snapshot_digest
                and r.cursor_checkpoint.sequence <= checkpoint.sequence
                for r in records))
    gates.append(PromotionGateEvidence("final_delta_checkpoint", delta_ready,
        final_delta.evidence_digest if final_delta else None,
        None if delta_ready else "final_delta_checkpoint_missing_or_mismatched"))
    gates.append(PromotionGateEvidence("legacy_writer_fence", fence_ready,
        legacy_fence.fence_digest if legacy_fence else None,
        None if fence_ready else "legacy_writer_not_fenced_for_generation"))
    gates.append(PromotionGateEvidence("zero_unknown_and_unresolved", unknown_mappings == 0
        and unresolved_positive_bindings == 0 and not bad and not conflicts
        and not duplicate_instance,
        rows_digest if not bad and not conflicts else None,
        None if unknown_mappings == 0 and unresolved_positive_bindings == 0 and not bad
        and not conflicts and not duplicate_instance
        else "unknown_or_unresolved_instance_evidence"))
    gates.append(PromotionGateEvidence("parity", parity_ready,
        parity.evidence_digest if parity else None,
        None if parity_ready else "parity_evidence_missing_or_mismatched"))
    gates.append(PromotionGateEvidence("final_delta_and_instance_evidence", imports_ready,
        rows_digest if accepted else None, None if imports_ready else "final_delta_or_instance_evidence_incomplete"))
    ready = (upstream is not None and upstream.ready_for_positive_transition(partition)
        and imports_ready and writer_generation is not None
        and all(g.passed and _valid_digest(g.evidence_digest) for g in gates))
    status = HandoffStatus.READY if ready else HandoffStatus.BLOCKED
    return PromotionRecord(record_id, "MIG-M05-02", status, partition,
        checkpoint.source_snapshot_digest, rows_digest, checkpoint, len(imported), len(replayed),
        len(bad), len(conflicts), tuple(gates), actor, recorded_at, decision_owner,
        writer_generation, correlation_id, status, "legacy", False,
        final_delta_checkpoint_digest=final_delta.checkpoint.digest if final_delta else None,
        retry_evidence_digest=retry_digest,
        parity_evidence_digest=parity.evidence_digest if parity else None,
        legacy_writer_fenced=fence_ready, unknown_mappings=unknown_mappings,
        unresolved_positive_bindings=unresolved_positive_bindings)


class M05PromotionService:
    def __init__(self, port: M05PromotionRecordPort) -> None:
        self._port = port

    async def record_m06_handoff(
        self, *, record_id: str, records: Sequence[InstanceImportRecord], checkpoint: ImportCheckpoint,
        upstream: M05UpstreamEvidence | None, actor: str, recorded_at: str, decision_owner: str,
        writer_generation: int | None, correlation_id: str,
        final_delta: FinalDeltaEvidence | None, parity: ParityEvidence | None,
        legacy_fence: LegacyWriterFenceEvidence | None, unknown_mappings: int = 0,
        unresolved_positive_bindings: int = 0,
        retry_attempts: Sequence[QuarantineRetryAttempt] = (),
    ) -> PromotionRecord:
        record = build_m06_handoff_record(
            record_id=record_id, records=records, checkpoint=checkpoint, upstream=upstream,
            actor=actor, recorded_at=recorded_at, decision_owner=decision_owner,
            writer_generation=writer_generation, correlation_id=correlation_id,
            final_delta=final_delta, parity=parity, legacy_fence=legacy_fence,
            unknown_mappings=unknown_mappings,
            unresolved_positive_bindings=unresolved_positive_bindings,
            retry_attempts=retry_attempts,
        )
        await self._port.append_immutable(record)
        return record


__all__ = [name for name in globals() if not name.startswith("_")]
