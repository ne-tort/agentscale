"""Immutable contracts for the MIG-RES-01 resource lifecycle."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping as MappingABC, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping, Protocol, cast

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_RAW_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SECRET_MARKERS = ("password", "secret", "token", "api_key", "private_key")
_REF_SUFFIXES = ("_ref", "_reference")
_MCP_TRANSPORTS = frozenset({"stdio", "http", "sse"})


def _json_value(value: object) -> object:
    if isinstance(value, MappingABC):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def freeze_json(value: object) -> object:
    if isinstance(value, MappingABC):
        return MappingProxyType({str(k): freeze_json(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(v) for v in value)
    if value is None or isinstance(value, (str, int, float, bool, bytes)):
        return value
    raise TypeError(f"unsupported payload value: {type(value).__name__}")


def canonical_digest(value: object) -> str:
    raw = json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_asset_digest(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("asset digest is required")
    if _SHA256.fullmatch(value):
        return value
    if _RAW_SHA256.fullmatch(value):
        return "sha256:" + value
    raise ValueError("asset digest must be sha256:<64 lowercase hex>")


def is_digest(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256.fullmatch(value))


def is_secret_reference(value: object) -> bool:
    if isinstance(value, str):
        return value.startswith(("vault://", "file://", "secret://", "${"))
    if isinstance(value, MappingABC):
        provider = value.get("provider")
        locator = value.get("locator") or value.get("path")
        return isinstance(provider, str) and bool(provider.strip()) and isinstance(locator, str) and bool(locator.strip())
    return False


def reject_secret_material(value: object) -> None:
    """Reject plaintext credential material while allowing external references."""
    if isinstance(value, MappingABC):
        for key, item in value.items():
            name = str(key).lower()
            marked = any(marker in name for marker in _SECRET_MARKERS)
            reference_key = name in {"secret_ref", "token_ref", "api_key_ref", "password_ref"} or (marked and name.endswith(_REF_SUFFIXES))
            if marked and not is_secret_reference(item) and not reference_key:
                raise ValueError("secret values are forbidden; use an external reference")
            if reference_key and not is_secret_reference(item):
                raise ValueError("secret reference fields must contain an external reference")
            reject_secret_material(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            reject_secret_material(item)
    elif isinstance(value, str) and value.lower().startswith(("password=", "secret=", "token=", "api_key=")):
        raise ValueError("inline secret material is forbidden")


def safe_workspace_path(value: object) -> bool:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return False
    path = value.replace("\\", "/")
    if path.startswith("/") or path == ".." or path.startswith("../") or "/../" in path:
        return False
    if re.match(r"^[A-Za-z]:", path):
        return False
    return all(part not in {"", "."} for part in path.split("/"))


class ResourceKind(StrEnum):
    PROMPT = "prompt"
    FILE = "file"
    MCP = "mcp"
    # Compatibility values from the pre-MIG-RES contract.
    CONNECTION = "connection"
    CREDENTIAL = "credential"
    CONFIGURATION = "configuration"


class ResourceLifecycle(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"
    QUARANTINED = "quarantined"


class VersionStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"


class ScopeKind(StrEnum):
    PLATFORM = "platform"
    TENANT = "tenant"
    COMPANY = "company"
    CABINET = "cabinet"
    PROJECT = "project"
    SESSION = "session"


class BindingState(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class BindingKind(StrEnum):
    LOCAL = "local"
    GLOBAL = "global"


class ProjectSelectionPolicy(StrEnum):
    EXPLICIT_PROJECTS = "explicit_projects"
    ALL_BOUND_PROJECTS = "all_bound_projects"


SelectionPolicy = ProjectSelectionPolicy


class ImportOutcome(StrEnum):
    IMPORTED = "imported"
    ACCEPTED = "imported"
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
        if not is_secret_reference(f"{self.provider}://{self.locator}"):
            raise ValueError("secret locator must remain an external reference")


@dataclass(frozen=True, slots=True)
class AssetReference:
    asset_id: str
    version_id: str
    digest: str
    storage_key: str
    filename: str
    content_type: str | None = None
    size: int | None = None

    def __post_init__(self) -> None:
        if not all(v.strip() for v in (self.asset_id, self.version_id, self.storage_key, self.filename)):
            raise ValueError("asset identity, storage key and filename are required")
        object.__setattr__(self, "digest", normalize_asset_digest(self.digest))
        if self.size is not None and self.size < 0:
            raise ValueError("asset size cannot be negative")

    @classmethod
    def from_mapping(cls, value: MappingABC[str, object]) -> "AssetReference":
        version_id = value.get("version_id", value.get("blob_version_id"))
        digest = value.get("digest", value.get("sha256"))
        required = (value.get("asset_id"), version_id, digest, value.get("storage_key"), value.get("filename"))
        if not all(isinstance(v, str) for v in required):
            raise ValueError("asset reference is incomplete")
        size = value.get("size")
        if size is not None and not isinstance(size, int):
            raise ValueError("asset size must be an integer")
        mime = value.get("content_type", value.get("mime"))
        return cls(
            cast(str, value["asset_id"]),
            cast(str, version_id),
            cast(str, digest),
            cast(str, value["storage_key"]),
            cast(str, value["filename"]),
            mime if isinstance(mime, str) else None,
            size,
        )


@dataclass(frozen=True, slots=True)
class ResourceScope:
    kind: ScopeKind
    scope_id: str
    tenant_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ScopeKind(self.kind))
        if not self.scope_id.strip() or not self.tenant_id.strip():
            raise ValueError("scope and tenant ids are required")
        if self.kind is ScopeKind.TENANT and self.scope_id != self.tenant_id:
            raise ValueError("tenant scope id must equal tenant id")

    @property
    def id(self) -> str:
        return self.scope_id


@dataclass(frozen=True, slots=True)
class ResourceVersion:
    version_id: str
    sequence: int
    schema_version: str
    content_digest: str
    payload: Mapping[str, object]
    secret_references: tuple[SecretReference, ...] = ()
    resource_id: str | None = None
    status: VersionStatus = VersionStatus.PUBLISHED
    created_at: datetime | None = None
    created_by: str | None = None

    def __post_init__(self) -> None:
        if not all(v.strip() for v in (self.version_id, self.schema_version, self.content_digest)) or self.sequence < 1:
            raise ValueError("version identity, schema and positive sequence are required")
        if not is_digest(self.content_digest):
            raise ValueError("content_digest must be sha256:<64 lowercase hex>")
        if self.resource_id is not None and not self.resource_id.strip():
            raise ValueError("resource_id cannot be blank")
        object.__setattr__(self, "status", VersionStatus(self.status))
        payload = freeze_json(self.payload)
        object.__setattr__(self, "payload", payload)
        reject_secret_material(payload)
        if canonical_digest(payload) != self.content_digest:
            raise ValueError("content_digest does not match immutable payload")
        object.__setattr__(self, "secret_references", tuple(self.secret_references))

    @property
    def number(self) -> int:
        return self.sequence

    @property
    def asset_digest(self) -> str | None:
        payload = self.payload
        raw = payload.get("asset_ref", payload.get("file_ref", payload.get("asset"))) if isinstance(payload, MappingABC) else None
        if isinstance(raw, MappingABC):
            try:
                return AssetReference.from_mapping(raw).digest
            except ValueError:
                return None
        if isinstance(payload, MappingABC):
            for key in ("body_md", "body", "content"):
                body = payload.get(key)
                if isinstance(body, str):
                    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
        return None

    def with_status(self, status: VersionStatus) -> "ResourceVersion":
        return replace(self, status=status)


@dataclass(frozen=True, slots=True)
class Resource:
    resource_id: str
    kind: ResourceKind
    scope: ResourceScope
    lifecycle: ResourceLifecycle
    versions: tuple[ResourceVersion, ...]
    metadata: Mapping[str, object] = field(default_factory=dict)
    active_version: int | None = None
    source_kind: str | None = None
    source_id: str | None = None
    source_digest: str | None = None

    def __post_init__(self) -> None:
        if not self.resource_id.strip() or not self.versions:
            raise ValueError("resource identity and at least one version are required")
        object.__setattr__(self, "kind", ResourceKind(self.kind))
        object.__setattr__(self, "lifecycle", ResourceLifecycle(self.lifecycle))
        object.__setattr__(self, "metadata", freeze_json(self.metadata))
        versions = tuple(self.versions)
        sequences = [v.sequence for v in versions]
        ids = [v.version_id for v in versions]
        if sequences != sorted(set(sequences)) or len(ids) != len(set(ids)):
            raise ValueError("resource versions must be unique and monotonically ordered")
        if any(v.resource_id not in (None, self.resource_id) for v in versions):
            raise ValueError("version belongs to another resource")
        if self.active_version is not None and self.active_version not in sequences:
            raise ValueError("active_version must identify an existing version")
        if self.source_digest is not None and not is_digest(self.source_digest):
            raise ValueError("source_digest must be sha256")

    @property
    def profile_id(self) -> str | None:
        value = self.metadata.get("profile_id")
        return value if isinstance(value, str) and value.strip() else None

    def version_by_id(self, version_id: str) -> ResourceVersion | None:
        return next((v for v in self.versions if v.version_id == version_id), None)

    def version(self, sequence: int) -> ResourceVersion | None:
        return next((v for v in self.versions if v.sequence == sequence), None)

    def with_appended_version(self, version: ResourceVersion) -> "Resource":
        if version.resource_id not in (None, self.resource_id) or version.sequence <= self.versions[-1].sequence:
            raise ValueError("resource versions are append-only")
        if any(v.version_id == version.version_id for v in self.versions):
            raise ValueError("resource version identity already exists")
        return replace(self, versions=self.versions + (version,))

    def publish_version(self, version_id: str) -> "Resource":
        if self.lifecycle in {ResourceLifecycle.ARCHIVED, ResourceLifecycle.QUARANTINED}:
            raise ValueError("archived or quarantined resource cannot be published")
        selected = self.version_by_id(version_id)
        if selected is None:
            raise ValueError("version does not exist")
        versions = tuple(v.with_status(VersionStatus.PUBLISHED if v.version_id == version_id else VersionStatus.RETIRED) for v in self.versions)
        return replace(self, lifecycle=ResourceLifecycle.ACTIVE, versions=versions, active_version=selected.sequence)

    def quarantine(self) -> "Resource":
        return replace(self, lifecycle=ResourceLifecycle.QUARANTINED)

    def archive(self) -> "Resource":
        return replace(self, lifecycle=ResourceLifecycle.ARCHIVED)


@dataclass(frozen=True, slots=True)
class Binding:
    binding_id: str
    resource_id: str
    version_id: str
    scope: ResourceScope
    state: BindingState
    session_id: str | None = None
    project_id: str | None = None
    target_scope: ResourceScope | None = None
    project_ids: tuple[str, ...] | None = None
    selection_policy: ProjectSelectionPolicy | None = None
    profile_id: str | None = None
    priority: int = 0
    generation: int = 1
    binding_kind: BindingKind = BindingKind.LOCAL

    def __post_init__(self) -> None:
        if not all(v.strip() for v in (self.binding_id, self.resource_id, self.version_id)):
            raise ValueError("explicit binding identity is required")
        object.__setattr__(self, "state", BindingState(self.state))
        object.__setattr__(self, "binding_kind", BindingKind(self.binding_kind))
        if self.target_scope is not None and self.target_scope.tenant_id != self.scope.tenant_id:
            raise ValueError("binding target cannot cross tenants")
        if self.project_ids is not None:
            object.__setattr__(self, "project_ids", tuple(self.project_ids))
        if self.selection_policy is not None:
            object.__setattr__(self, "selection_policy", ProjectSelectionPolicy(self.selection_policy))
        if self.generation < 1:
            raise ValueError("binding generation must be positive")

    @property
    def effective_target_scope(self) -> ResourceScope:
        if self.target_scope is not None:
            return self.target_scope
        if self.project_id is not None:
            return ResourceScope(ScopeKind.PROJECT, self.project_id, self.scope.tenant_id)
        if self.session_id is not None:
            return ResourceScope(ScopeKind.SESSION, self.session_id, self.scope.tenant_id)
        return self.scope

    def policy_errors(self) -> tuple[str, ...]:
        target = self.effective_target_scope
        errors: list[str] = []
        if target.tenant_id != self.scope.tenant_id:
            errors.append("cross_tenant_binding")
        if self.project_ids is not None and len(set(self.project_ids)) != len(self.project_ids):
            errors.append("duplicate_project_binding")
        if target.kind is ScopeKind.PROJECT:
            if self.selection_policy is None:
                errors.append("ambiguous_project_selection")
            elif self.selection_policy is ProjectSelectionPolicy.EXPLICIT_PROJECTS:
                if not self.project_ids:
                    errors.append("explicit_project_selection_empty")
                elif target.scope_id not in self.project_ids:
                    errors.append("project_not_explicitly_bound")
            elif self.project_ids not in (None, ()):
                errors.append("all_bound_policy_has_explicit_projects")
        elif self.project_ids is not None or self.selection_policy is not None:
            errors.append("project_policy_on_non_project_binding")
        if target.kind is ScopeKind.SESSION and self.session_id not in (None, target.scope_id):
            errors.append("session_binding_mismatch")
        if target.kind is ScopeKind.PROJECT and self.project_id not in (None, target.scope_id):
            errors.append("project_binding_mismatch")
        return tuple(dict.fromkeys(errors))

    def matches_project(self, project_id: str, bound_project_ids: tuple[str, ...] | None = None) -> bool:
        target = self.effective_target_scope
        if target.kind is ScopeKind.PROJECT and target.scope_id == project_id and self.selection_policy is None:
            return True
        if self.selection_policy is ProjectSelectionPolicy.EXPLICIT_PROJECTS:
            return project_id in (self.project_ids or ())
        if self.selection_policy is ProjectSelectionPolicy.ALL_BOUND_PROJECTS:
            return bound_project_ids is not None and project_id in bound_project_ids
        return False


ResourceBinding = Binding
ContentResource = Resource


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
    target_scope: ResourceScope | None = None

    def __post_init__(self) -> None:
        if not all(v.strip() for v in (self.tenant_id, self.subject_id, self.scope_id, self.proof_id)) or self.generation < 1:
            raise ValueError("complete positive scope proof is required")
        object.__setattr__(self, "kind", ScopeKind(self.kind))
        if self.kind is ScopeKind.PROJECT and self.project_id != self.scope_id:
            raise ValueError("project proof must identify its project")
        if self.kind is ScopeKind.SESSION and self.session_id != self.scope_id:
            raise ValueError("session proof must identify its session")
        target = self.target_scope or ResourceScope(self.kind, self.scope_id, self.tenant_id)
        if target.tenant_id != self.tenant_id or target.scope_id != self.scope_id:
            raise ValueError("scope proof target mismatch")
        object.__setattr__(self, "target_scope", target)


@dataclass(frozen=True, slots=True)
class ResourceProvenance:
    resource_id: str
    version_id: str
    binding_id: str
    source_kind: str
    source_id: str
    source_digest: str
    asset_digest: str | None
    scope_proof_id: str
    generation: int

    def __post_init__(self) -> None:
        if not all(v.strip() for v in (self.resource_id, self.version_id, self.binding_id, self.source_kind, self.source_id, self.scope_proof_id)) or not is_digest(self.source_digest):
            raise ValueError("complete resource provenance is required")
        if self.asset_digest is not None:
            object.__setattr__(self, "asset_digest", normalize_asset_digest(self.asset_digest))
        if self.generation < 1:
            raise ValueError("provenance generation must be positive")


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    binding: Binding
    resource: Resource
    version: ResourceVersion
    scope_proof: ScopeProof
    generation: int
    provenance: ResourceProvenance | None = None

    def __post_init__(self) -> None:
        target = self.binding.effective_target_scope
        if self.generation < 1 or self.scope_proof.generation != self.generation:
            raise ValueError("snapshot and scope proof generations must match")
        if (self.binding.resource_id != self.resource.resource_id or self.binding.version_id != self.version.version_id or self.binding.scope != self.resource.scope or self.scope_proof.tenant_id != self.binding.scope.tenant_id or self.scope_proof.kind is not target.kind or self.scope_proof.scope_id != target.scope_id or self.scope_proof.target_scope != target):
            raise ValueError("snapshot components do not form one consistent proof")
        if self.version.resource_id not in (None, self.resource.resource_id):
            raise ValueError("snapshot version belongs to another resource")
        provenance = self.provenance
        if provenance is None:
            provenance = ResourceProvenance(self.resource.resource_id, self.version.version_id, self.binding.binding_id, self.resource.source_kind or f"resource:{self.resource.kind.value}", self.resource.source_id or self.resource.resource_id, self.resource.source_digest or self.version.content_digest, self.version.asset_digest, self.scope_proof.proof_id, self.generation)
        elif (provenance.resource_id, provenance.version_id, provenance.binding_id, provenance.scope_proof_id, provenance.generation) != (self.resource.resource_id, self.version.version_id, self.binding.binding_id, self.scope_proof.proof_id, self.generation):
            raise ValueError("snapshot provenance identity mismatch")
        object.__setattr__(self, "provenance", provenance)


@dataclass(frozen=True, slots=True)
class ResourceResolution:
    snapshot: ResourceSnapshot | None
    reason: str
    provenance: ResourceProvenance | None = None

    def __post_init__(self) -> None:
        if self.snapshot is not None and self.provenance is None:
            object.__setattr__(self, "provenance", self.snapshot.provenance)

    @property
    def resolved(self) -> bool:
        return self.snapshot is not None

    @property
    def resource(self) -> Resource | None:
        return self.snapshot.resource if self.snapshot else None

    @property
    def binding(self) -> Binding | None:
        return self.snapshot.binding if self.snapshot else None

    @property
    def version(self) -> ResourceVersion | None:
        return self.snapshot.version if self.snapshot else None


@dataclass(frozen=True, slots=True)
class CompatibilityCandidate:
    source_kind: str
    source_id: str
    schema_version: str
    digest: str
    resource: Resource
    binding: Binding
    source_envelope: Mapping[str, object]

    def __post_init__(self) -> None:
        if not all(v.strip() for v in (self.source_kind, self.source_id, self.schema_version)):
            raise ValueError("source identity and schema are required")
        if self.digest and not is_digest(self.digest):
            raise ValueError("candidate digest must be sha256")
        envelope = freeze_json(self.source_envelope)
        object.__setattr__(self, "source_envelope", envelope)
        reject_secret_material(envelope)


@dataclass(frozen=True, slots=True)
class ImportCheckpoint:
    partition_id: str
    tenant_id: str
    source_snapshot_id: str
    source_schema_version: str
    after_source_id: str | None
    sequence: int
    source_snapshot_digest: str
    complete: bool = False

    def __post_init__(self) -> None:
        if not all(v.strip() for v in (self.partition_id, self.tenant_id, self.source_snapshot_id, self.source_schema_version)) or self.sequence < 0 or not is_digest(self.source_snapshot_digest):
            raise ValueError("invalid import checkpoint")


@dataclass(frozen=True, slots=True)
class ImportRecord:
    source_kind: str
    source_id: str
    schema_version: str
    digest: str
    outcome: ImportOutcome
    reason: str | None
    checkpoint: ImportCheckpoint | None = None
    asset_digests: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not all(v.strip() for v in (self.source_kind, self.source_id, self.schema_version)) or not is_digest(self.digest):
            raise ValueError("invalid import record identity")
        object.__setattr__(self, "outcome", ImportOutcome(self.outcome))
        object.__setattr__(self, "asset_digests", tuple(self.asset_digests))


@dataclass(frozen=True, slots=True)
class ResourceImportPartition:
    partition_id: str
    tenant_id: str
    source_snapshot_id: str
    source_schema_version: str
    source_snapshot_digest: str

    def __post_init__(self) -> None:
        if not all(v.strip() for v in (self.partition_id, self.tenant_id, self.source_snapshot_id, self.source_schema_version)) or not is_digest(self.source_snapshot_digest):
            raise ValueError("invalid import partition")

    def initial_checkpoint(self) -> ImportCheckpoint:
        return ImportCheckpoint(self.partition_id, self.tenant_id, self.source_snapshot_id, self.source_schema_version, None, 0, self.source_snapshot_digest)


@dataclass(frozen=True, slots=True)
class ResourceImportContext:
    tenant_id: str | None
    m04_scope_ready: bool
    scope_port: object | None
    correlation_id: str

    @property
    def usable(self) -> bool:
        return bool(self.tenant_id and self.m04_scope_ready and self.scope_port and self.correlation_id.strip())


@dataclass(frozen=True, slots=True)
class ResourceImportPage:
    records: tuple[ImportRecord, ...]
    checkpoint: ImportCheckpoint
    complete: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ProfileCandidate:
    profile_id: str
    resource_id: str
    binding_id: str
    tenant_id: str
    project_ids: tuple[str, ...] | None
    selection_policy: ProjectSelectionPolicy | None
    priority: int
    version_number: int
    active: bool = True
    lifecycle: ResourceLifecycle = ResourceLifecycle.ACTIVE
    generation: int = 1
    bound_project_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not all(v.strip() for v in (self.profile_id, self.resource_id, self.binding_id, self.tenant_id)) or self.version_number < 1 or self.generation < 1:
            raise ValueError("invalid profile candidate")
        if self.project_ids is not None:
            object.__setattr__(self, "project_ids", tuple(self.project_ids))
        if self.bound_project_ids is not None:
            object.__setattr__(self, "bound_project_ids", tuple(self.bound_project_ids))
        if self.selection_policy is not None:
            object.__setattr__(self, "selection_policy", ProjectSelectionPolicy(self.selection_policy))
        object.__setattr__(self, "lifecycle", ResourceLifecycle(self.lifecycle))

    def policy_error(self) -> str | None:
        if self.selection_policy is None:
            return "ambiguous_project_selection"
        if self.selection_policy is ProjectSelectionPolicy.EXPLICIT_PROJECTS and not self.project_ids:
            return "explicit_project_selection_empty"
        if self.selection_policy is ProjectSelectionPolicy.ALL_BOUND_PROJECTS and self.bound_project_ids is None:
            return "bound_project_selection_evidence_missing"
        if self.project_ids is not None and len(set(self.project_ids)) != len(self.project_ids):
            return "duplicate_project_binding"
        return None

    def matches(self, project_id: str) -> bool:
        if not self.active or self.lifecycle is not ResourceLifecycle.ACTIVE or self.policy_error() is not None:
            return False
        if self.selection_policy is ProjectSelectionPolicy.EXPLICIT_PROJECTS:
            return project_id in (self.project_ids or ())
        return project_id in (self.bound_project_ids or ())

    @property
    def specificity(self) -> int:
        return 1 if self.selection_policy is ProjectSelectionPolicy.EXPLICIT_PROJECTS else 0


@dataclass(frozen=True, slots=True)
class ProfileSelection:
    selected: ProfileCandidate | None
    candidates: tuple[ProfileCandidate, ...]
    reason: str
    candidates_digest: str
    selection_rank: tuple[object, ...] | None = None

    @property
    def resolved(self) -> bool:
        return self.selected is not None and self.reason == "resolved"


class TrustedResourceImportPort(Protocol):
    async def import_if_absent_or_record_conflict(self, *, identity: tuple[str, str, str], digest: str, candidate: CompatibilityCandidate) -> ImportRecord: ...


class ResumableResourceImportPort(TrustedResourceImportPort, Protocol):
    async def load_checkpoint(self, *, partition: ResourceImportPartition) -> ImportCheckpoint | None: ...
    async def import_row(self, *, identity: tuple[str, str, str], digest: str, candidate: CompatibilityCandidate, checkpoint: ImportCheckpoint, quarantine_reason: str | None, correlation_id: str) -> ImportRecord: ...
    async def commit_checkpoint(self, *, partition: ResourceImportPartition, expected_sequence: int, checkpoint: ImportCheckpoint) -> bool: ...


class ResourceImportSourcePort(Protocol):
    async def read_page(self, *, partition: ResourceImportPartition, after_source_id: str | None, limit: int) -> Sequence[CompatibilityCandidate]: ...


class ResourceImportScopePort(Protocol):
    async def prove_import_scope(self, *, tenant_id: str, owner_scope: ResourceScope, target_scope: ResourceScope, project_ids: tuple[str, ...] | None, selection_policy: ProjectSelectionPolicy | None) -> ScopeProof | None: ...


class TrustedResourceSnapshotPort(Protocol):
    async def resolve_snapshot(self, *, tenant_id: str, subject_id: str, session_id: str | None, project_id: str | None, binding_id: str, resource_id: str, version_id: str, profile_id: str | None = None) -> ResourceSnapshot | None: ...


class ResourceProfilePort(Protocol):
    async def list_profile_candidates(self, *, tenant_id: str, project_id: str, session_id: str | None) -> Sequence[ProfileCandidate]: ...


ResourceImportScopePortAlias = ResourceImportScopePort
TrustedResourceScopePort = ResourceImportScopePort
