"""Fail-closed typed resource lifecycle contracts for MIG-RES-01.

Positive import and resolution require trusted atomic persistence ports. No such
adapter is implemented in this boundary, so the public operations stay blocked.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Protocol


class ResourceKind(StrEnum):
    CONNECTION = "connection"
    CREDENTIAL = "credential"
    CONFIGURATION = "configuration"


class ResourceLifecycle(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"
    QUARANTINED = "quarantined"


class ScopeKind(StrEnum):
    TENANT = "tenant"
    SESSION = "session"
    PROJECT = "project"


class BindingState(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class ImportOutcome(StrEnum):
    IMPORTED = "imported"
    REPLAY = "replay"
    CONFLICT = "conflict"
    QUARANTINED = "quarantined"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class SecretReference:
    provider: str
    locator: str
    version: str | None = None

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.locator.strip():
            raise ValueError("secret provider and locator are required")
        if any(token in self.locator.lower() for token in ("password=", "secret=", "token=")):
            raise ValueError("secret values are forbidden; provide a reference")


@dataclass(frozen=True, slots=True)
class ResourceScope:
    kind: ScopeKind
    scope_id: str
    tenant_id: str

    def __post_init__(self) -> None:
        if not self.scope_id.strip() or not self.tenant_id.strip():
            raise ValueError("scope and tenant ids are required")
        if self.kind is ScopeKind.TENANT and self.scope_id != self.tenant_id:
            raise ValueError("tenant scope id must equal tenant id")


@dataclass(frozen=True, slots=True)
class ResourceVersion:
    version_id: str
    sequence: int
    schema_version: str
    content_digest: str
    payload: Mapping[str, object]
    secret_references: tuple[SecretReference, ...] = ()

    def __post_init__(self) -> None:
        if not all(v.strip() for v in (self.version_id, self.schema_version, self.content_digest)) or self.sequence < 1:
            raise ValueError("version identity, schema and positive sequence are required")
        if not self.content_digest.startswith("sha256:"):
            raise ValueError("content_digest must be sha256")
        _reject_secret_material(self.payload)


@dataclass(frozen=True, slots=True)
class Resource:
    resource_id: str
    kind: ResourceKind
    scope: ResourceScope
    lifecycle: ResourceLifecycle
    versions: tuple[ResourceVersion, ...]

    def __post_init__(self) -> None:
        if not self.resource_id.strip():
            raise ValueError("resource_id is required")
        if not self.versions:
            raise ValueError("a resource must have at least one immutable version")
        seqs = [v.sequence for v in self.versions]
        ids = [v.version_id for v in self.versions]
        if seqs != sorted(set(seqs)) or len(ids) != len(set(ids)):
            raise ValueError("versions must be unique and monotonically ordered")


@dataclass(frozen=True, slots=True)
class Binding:
    binding_id: str
    resource_id: str
    version_id: str
    scope: ResourceScope
    state: BindingState
    session_id: str | None = None
    project_id: str | None = None

    def __post_init__(self) -> None:
        if not all(x.strip() for x in (self.binding_id, self.resource_id, self.version_id)):
            raise ValueError("explicit binding identity is required")
        if self.scope.kind is ScopeKind.SESSION and self.session_id != self.scope.scope_id:
            raise ValueError("session binding must match its typed scope")
        if self.scope.kind is ScopeKind.PROJECT and self.project_id != self.scope.scope_id:
            raise ValueError("project binding must match its typed scope")


@dataclass(frozen=True, slots=True)
class ScopeProof:
    tenant_id: str
    subject_id: str
    kind: ScopeKind
    scope_id: str
    session_id: str | None
    project_id: str | None
    proof_id: str
    generation: int


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    binding: Binding
    resource: Resource
    version: ResourceVersion
    scope_proof: ScopeProof
    generation: int

    def __post_init__(self) -> None:
        if self.generation < 1 or self.scope_proof.generation != self.generation:
            raise ValueError("snapshot and scope proof generation must match")
        if (self.binding.resource_id != self.resource.resource_id
                or self.binding.version_id != self.version.version_id
                or self.binding.scope != self.resource.scope
                or self.scope_proof.tenant_id != self.binding.scope.tenant_id
                or self.scope_proof.kind is not self.binding.scope.kind
                or self.scope_proof.scope_id != self.binding.scope.scope_id):
            raise ValueError("snapshot components do not form one consistent proof")


@dataclass(frozen=True, slots=True)
class ResourceResolution:
    snapshot: ResourceSnapshot | None
    reason: str

    @property
    def resource(self) -> Resource | None:
        return self.snapshot.resource if self.snapshot else None

    @property
    def binding(self) -> Binding | None:
        return self.snapshot.binding if self.snapshot else None

    @property
    def version(self) -> ResourceVersion | None:
        return self.snapshot.version if self.snapshot else None

    @property
    def resolved(self) -> bool:
        return self.snapshot is not None


@dataclass(frozen=True, slots=True)
class CompatibilityCandidate:
    source_kind: str
    source_id: str
    schema_version: str
    digest: str
    resource: Resource
    binding: Binding
    source_envelope: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ImportRecord:
    source_kind: str
    source_id: str
    schema_version: str
    digest: str
    outcome: ImportOutcome
    reason: str | None


class TrustedResourceImportPort(Protocol):
    """One transaction: unique identity CAS plus immutable writes/conflict evidence."""
    async def import_if_absent_or_record_conflict(self, *, identity: tuple[str, str, str],
            digest: str, candidate: CompatibilityCandidate) -> ImportRecord: ...


class TrustedResourceSnapshotPort(Protocol):
    """Tenant-scoped consistent snapshot, including exact version and scope proof."""
    async def resolve_snapshot(self, *, tenant_id: str, subject_id: str,
            session_id: str | None, project_id: str | None, binding_id: str,
            resource_id: str, version_id: str) -> ResourceSnapshot | None: ...


def _reject_secret_material(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key).lower()
            if any(word in key_text for word in ("password", "secret", "token", "api_key", "private_key")):
                raise ValueError("resource payload may contain secret references only")
            _reject_secret_material(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_secret_material(item)
    elif isinstance(value, str) and value.lower().startswith(("password=", "token=", "secret=")):
        raise ValueError("resource payload may contain secret references only")


def canonical_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def candidate_canonical_digest(candidate: CompatibilityCandidate) -> str:
    """Hash source envelope, full resource versions, and binding identity together."""
    return canonical_digest({
        "source": candidate.source_envelope,
        "source_identity": {
            "kind": candidate.source_kind,
            "id": candidate.source_id,
            "schema": candidate.schema_version,
        },
        "resource": {
            "id": candidate.resource.resource_id,
            "kind": candidate.resource.kind.value,
            "scope": _canonical_scope(candidate.resource.scope),
            "lifecycle": candidate.resource.lifecycle.value,
            "versions": [_canonical_version(v) for v in candidate.resource.versions],
        },
        "binding": {
            "id": candidate.binding.binding_id,
            "resource_id": candidate.binding.resource_id,
            "version_id": candidate.binding.version_id,
            "scope": _canonical_scope(candidate.binding.scope),
            "state": candidate.binding.state.value,
        },
    })


def _canonical_scope(scope: ResourceScope) -> dict[str, object]:
    return {"kind": scope.kind.value, "id": scope.scope_id, "tenant": scope.tenant_id}


def _canonical_version(version: ResourceVersion) -> dict[str, object]:
    return {"id": version.version_id, "sequence": version.sequence,
            "schema": version.schema_version, "content_digest": version.content_digest,
            "payload": version.payload,
            "secret_references": [{"provider": ref.provider, "locator": ref.locator,
                                  "version": ref.version} for ref in version.secret_references]}


async def resolve_resource(*, store: TrustedResourceSnapshotPort, scope_resolver: object | None,
                           m04_scope_ready: bool, tenant_id: str | None, subject_id: str | None,
                           session_id: str | None, project_id: str | None,
                           binding_id: str, resource_id: str, version_id: str) -> ResourceResolution:
    if not m04_scope_ready or scope_resolver is None:
        return ResourceResolution(None, "m04_scope_resolver_unavailable")
    if not all(v and v.strip() for v in (tenant_id, subject_id, binding_id, resource_id, version_id)):
        return ResourceResolution(None, "authenticated_scope_context_missing")
    # The resolver must be the single trusted read boundary. Independent reads or
    # an in-memory authorization check cannot prove a stable binding/version.
    if not hasattr(store, "resolve_snapshot"):
        return ResourceResolution(None, "trusted_snapshot_port_unavailable")
    snapshot = await store.resolve_snapshot(tenant_id=tenant_id, subject_id=subject_id,
        session_id=session_id, project_id=project_id, binding_id=binding_id,
        resource_id=resource_id, version_id=version_id)
    if snapshot is None:
        return ResourceResolution(None, "immutable_snapshot_unavailable_or_denied")
    if (snapshot.binding.binding_id != binding_id
            or snapshot.resource.resource_id != resource_id
            or snapshot.version.version_id != version_id
            or snapshot.scope_proof.tenant_id != tenant_id
            or snapshot.scope_proof.subject_id != subject_id
            or snapshot.scope_proof.session_id != session_id
            or snapshot.scope_proof.project_id != project_id
            or snapshot.binding.state is not BindingState.ACTIVE
            or snapshot.resource.lifecycle is not ResourceLifecycle.ACTIVE):
        return ResourceResolution(None, "immutable_snapshot_identity_or_lifecycle_mismatch")
    return ResourceResolution(snapshot, "resolved")


async def import_compatibility_candidate(*, store: TrustedResourceImportPort,
                                         candidate: CompatibilityCandidate,
                                         dependencies_ready: bool = False) -> ImportRecord:
    if not all(x.strip() for x in (candidate.source_kind, candidate.source_id, candidate.schema_version)):
        raise ValueError("source identity and schema are required")
    digest = candidate_canonical_digest(candidate)
    if digest != candidate.digest:
        return ImportRecord(candidate.source_kind, candidate.source_id, candidate.schema_version,
                            digest, ImportOutcome.QUARANTINED, "canonical_digest_mismatch")
    if not dependencies_ready:
        return ImportRecord(candidate.source_kind, candidate.source_id, candidate.schema_version,
                            digest, ImportOutcome.BLOCKED, "dependency_gate_missing")
    # No adapter in this boundary means positive import stays fail-closed.
    if not hasattr(store, "import_if_absent_or_record_conflict"):
        return ImportRecord(candidate.source_kind, candidate.source_id, candidate.schema_version,
                            digest, ImportOutcome.BLOCKED, "trusted_atomic_import_port_unavailable")
    if candidate.resource.scope.kind not in (ScopeKind.TENANT, ScopeKind.SESSION, ScopeKind.PROJECT):
        return ImportRecord(candidate.source_kind, candidate.source_id, candidate.schema_version,
                            digest, ImportOutcome.QUARANTINED, "unsupported_scope_kind")
    return await store.import_if_absent_or_record_conflict(
        identity=(candidate.source_kind, candidate.source_id, candidate.schema_version),
        digest=digest, candidate=candidate)
