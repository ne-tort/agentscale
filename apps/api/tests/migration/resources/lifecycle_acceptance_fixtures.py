"""MIG-RES-01 acceptance fixtures; tests remain intentionally unexecuted."""
from __future__ import annotations

from dataclasses import dataclass, replace

from prodavan.application.migration.resources import (
    AssetReference,
    Binding,
    BindingState,
    CompatibilityCandidate,
    ImportCheckpoint,
    ImportOutcome,
    ImportRecord,
    ProjectSelectionPolicy,
    Resource,
    ResourceImportPartition,
    ResourceKind,
    ResourceLifecycle,
    ResourceScope,
    ResourceSnapshot,
    ResourceVersion,
    ScopeKind,
    ScopeProof,
    VersionStatus,
    canonical_digest,
    candidate_canonical_digest,
)

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
CABINET_A = "cabinet-a"
PROJECT_A = "project-a"
PROJECT_B = "project-b"
SUBJECT_A = "subject-a"
ASSET_DIGEST = "sha256:" + "1" * 64
SNAPSHOT_DIGEST = "sha256:" + "2" * 64


def file_payload(*, digest: str = ASSET_DIGEST, path: str = "docs/readme.md") -> dict[str, object]:
    return {
        "file_ref": {
            "asset_id": "asset-readme",
            "version_id": "asset-version-1",
            "storage_key": "blobs/readme-1",
            "filename": "readme.md",
            "content_type": "text/markdown",
            "size": 12,
            "sha256": digest,
        },
        "asset_digest": digest,
        "path": path,
    }


def file_resource(*, tenant_id: str = TENANT_A, resource_id: str = "resource-file", payload: dict[str, object] | None = None) -> Resource:
    body = payload or file_payload()
    version = ResourceVersion(
        version_id=f"{resource_id}-v1",
        sequence=1,
        schema_version="resource-v1",
        content_digest=canonical_digest(body),
        payload=body,
        resource_id=resource_id,
        status=VersionStatus.PUBLISHED,
    )
    return Resource(
        resource_id=resource_id,
        kind=ResourceKind.FILE,
        scope=ResourceScope(ScopeKind.CABINET, CABINET_A, tenant_id),
        lifecycle=ResourceLifecycle.ACTIVE,
        versions=(version,),
        metadata={"profile_id": "profile-primary"},
        active_version=1,
        source_kind="legacy_file",
        source_id=f"legacy-{resource_id}",
        source_digest=canonical_digest({"source": resource_id, "payload": body}),
    )


def project_binding(resource: Resource, *, project_id: str = PROJECT_A, binding_id: str = "binding-file") -> Binding:
    return Binding(
        binding_id=binding_id,
        resource_id=resource.resource_id,
        version_id=resource.versions[-1].version_id,
        scope=resource.scope,
        state=BindingState.ACTIVE,
        target_scope=ResourceScope(ScopeKind.PROJECT, project_id, resource.scope.tenant_id),
        project_ids=(project_id,),
        selection_policy=ProjectSelectionPolicy.EXPLICIT_PROJECTS,
        profile_id="profile-primary",
        priority=10,
    )


def file_candidate(*, source_id: str = "source-file", tenant_id: str = TENANT_A, project_id: str = PROJECT_A) -> CompatibilityCandidate:
    resource = file_resource(tenant_id=tenant_id)
    binding = project_binding(resource, project_id=project_id)
    candidate = CompatibilityCandidate(
        source_kind="legacy_file",
        source_id=source_id,
        schema_version="resource-v1",
        digest="",
        resource=resource,
        binding=binding,
        source_envelope={"tenant_id": tenant_id, "project_id": project_id, "legacy_id": source_id},
    )
    return replace(candidate, digest=candidate_canonical_digest(candidate))


def project_proof(*, tenant_id: str = TENANT_A, project_id: str = PROJECT_A, generation: int = 1) -> ScopeProof:
    target = ResourceScope(ScopeKind.PROJECT, project_id, tenant_id)
    return ScopeProof(
        tenant_id=tenant_id,
        subject_id=SUBJECT_A,
        kind=ScopeKind.PROJECT,
        scope_id=project_id,
        session_id=None,
        project_id=project_id,
        proof_id=f"proof-{tenant_id}-{project_id}-{generation}",
        generation=generation,
        target_scope=target,
    )


def project_snapshot(*, tenant_id: str = TENANT_A, project_id: str = PROJECT_A) -> ResourceSnapshot:
    resource = file_resource(tenant_id=tenant_id)
    binding = project_binding(resource, project_id=project_id)
    return ResourceSnapshot(binding, resource, resource.versions[0], project_proof(tenant_id=tenant_id, project_id=project_id), 1)


@dataclass
class AtomicImportMemory:
    records: dict[tuple[str, str, str], ImportRecord]
    conflicts: list[ImportRecord]
    calls: list[tuple[tuple[str, str, str], str]]

    def __init__(self) -> None:
        self.records = {}
        self.conflicts = []
        self.calls = []

    async def import_if_absent_or_record_conflict(self, *, identity, digest, candidate):
        self.calls.append((identity, digest))
        prior = self.records.get(identity)
        if prior is not None:
            if prior.digest == digest:
                return replace(prior, outcome=ImportOutcome.REPLAY, reason="idempotent_replay")
            conflict = ImportRecord(candidate.source_kind, candidate.source_id, candidate.schema_version, digest, ImportOutcome.CONFLICT, "source_digest_changed")
            self.conflicts.append(conflict)
            return conflict
        record = ImportRecord(candidate.source_kind, candidate.source_id, candidate.schema_version, digest, ImportOutcome.IMPORTED, None, asset_digests=tuple(v.asset_digest for v in candidate.resource.versions if v.asset_digest))
        self.records[identity] = record
        return record


@dataclass
class ScopeMemory:
    wrong_tenant: bool = False
    calls: int = 0

    async def prove_import_scope(self, *, tenant_id, owner_scope, target_scope, project_ids, selection_policy):
        self.calls += 1
        if self.wrong_tenant:
            tenant_id = TENANT_B
        return ScopeProof(
            tenant_id=tenant_id,
            subject_id="migration",
            kind=target_scope.kind,
            scope_id=target_scope.scope_id,
            session_id=target_scope.scope_id if target_scope.kind is ScopeKind.SESSION else None,
            project_id=target_scope.scope_id if target_scope.kind is ScopeKind.PROJECT else None,
            proof_id="import-proof",
            generation=1,
            target_scope=target_scope,
        )


@dataclass
class PageSource:
    candidates: tuple[CompatibilityCandidate, ...]

    async def read_page(self, *, partition: ResourceImportPartition, after_source_id: str | None, limit: int):
        rows = sorted(self.candidates, key=lambda item: item.source_id)
        if after_source_id is not None:
            rows = [item for item in rows if item.source_id > after_source_id]
        return rows[:limit]


@dataclass
class ResumableImportMemory(AtomicImportMemory):
    checkpoint: ImportCheckpoint | None = None

    def __init__(self) -> None:
        super().__init__()
        self.checkpoint = None

    async def load_checkpoint(self, *, partition):
        return self.checkpoint

    async def import_row(self, *, identity, digest, candidate, checkpoint, quarantine_reason, correlation_id):
        prior = self.records.get(identity)
        if prior is not None and prior.digest == digest:
            record = replace(prior, outcome=ImportOutcome.REPLAY, reason="idempotent_replay", checkpoint=checkpoint)
        elif prior is not None:
            record = ImportRecord(candidate.source_kind, candidate.source_id, candidate.schema_version, digest, ImportOutcome.CONFLICT, "source_digest_changed", checkpoint=checkpoint)
            self.conflicts.append(record)
        else:
            outcome = ImportOutcome.QUARANTINED if quarantine_reason else ImportOutcome.IMPORTED
            record = ImportRecord(candidate.source_kind, candidate.source_id, candidate.schema_version, digest, outcome, quarantine_reason, checkpoint=checkpoint, asset_digests=tuple(v.asset_digest for v in candidate.resource.versions if v.asset_digest))
            if outcome is ImportOutcome.IMPORTED:
                self.records[identity] = record
        self.checkpoint = checkpoint
        return record

    async def commit_checkpoint(self, *, partition, expected_sequence, checkpoint):
        if self.checkpoint is None or self.checkpoint.sequence != expected_sequence:
            return False
        self.checkpoint = checkpoint
        return True


@dataclass
class SnapshotMemory:
    snapshot: ResourceSnapshot | None
    calls: list[dict[str, object]]

    def __init__(self, snapshot=None):
        self.snapshot = snapshot
        self.calls = []

    async def resolve_snapshot(self, **identity):
        self.calls.append(identity)
        return self.snapshot


class AuthorizedScope:
    async def is_authorized(self, **context):
        return True
