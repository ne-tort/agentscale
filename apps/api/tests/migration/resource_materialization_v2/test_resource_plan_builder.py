"""Acceptance tests for MIG-RESOURCE-MATERIALIZER-01; intentionally not executed."""
from __future__ import annotations

from dataclasses import replace

import pytest

from prodavan.application.migration.resources import (
    Binding,
    BindingState,
    ProjectSelectionPolicy,
    Resource,
    ResourceKind,
    ResourceLifecycle,
    ResourceScope,
    ResourceSnapshot,
    ResourceVersion,
    ScopeKind,
    ScopeProof,
    VersionStatus,
    canonical_digest,
)
from prodavan.application.migration.resource_materialization_v2 import (
    FileRefAclEvidence,
    ResourceOperationReadiness,
    ResourcePlanBuilder,
    ResourcePlanPorts,
    ResourcePlanRequest,
    ResourcePlanSelection,
    UpstreamEvidence,
)

TENANT = "tenant-a"
PROJECT = "project-a"
SUBJECT = "subject-a"
GENERATION = 4
DIGEST = "sha256:" + "a" * 64


def _snapshot(
    resource_id: str,
    kind: ResourceKind,
    payload: dict[str, object],
    *,
    binding_id: str | None = None,
    path: str | None = None,
    priority: int = 0,
) -> ResourceSnapshot:
    resource_id = resource_id
    version_id = f"{resource_id}-v1"
    if path is not None:
        payload = {**payload, "path": path}
    version = ResourceVersion(
        version_id=version_id,
        sequence=1,
        schema_version="resource-v1",
        content_digest=canonical_digest(payload),
        payload=payload,
        resource_id=resource_id,
        status=VersionStatus.PUBLISHED,
    )
    scope = ResourceScope(ScopeKind.CABINET, "cabinet-a", TENANT)
    project_scope = ResourceScope(ScopeKind.PROJECT, PROJECT, TENANT)
    resource = Resource(
        resource_id=resource_id,
        kind=kind,
        scope=scope,
        lifecycle=ResourceLifecycle.ACTIVE,
        versions=(version,),
        metadata={"profile_id": "profile-a"},
        active_version=1,
        source_kind="legacy_resource",
        source_id=f"source-{resource_id}",
        source_digest=DIGEST,
    )
    binding = Binding(
        binding_id=binding_id or f"binding-{resource_id}",
        resource_id=resource_id,
        version_id=version_id,
        scope=scope,
        state=BindingState.ACTIVE,
        target_scope=project_scope,
        project_ids=(PROJECT,),
        selection_policy=ProjectSelectionPolicy.EXPLICIT_PROJECTS,
        profile_id="profile-a",
        priority=priority,
    )
    proof = ScopeProof(
        tenant_id=TENANT,
        subject_id=SUBJECT,
        kind=ScopeKind.PROJECT,
        scope_id=PROJECT,
        session_id=None,
        project_id=PROJECT,
        proof_id=f"scope-proof-{resource_id}",
        generation=GENERATION,
        target_scope=project_scope,
    )
    return ResourceSnapshot(binding, resource, version, proof, GENERATION)


def _selection(snapshot: ResourceSnapshot, *, target: str | None = None, required: bool = True) -> ResourcePlanSelection:
    return ResourcePlanSelection(
        resource_id=snapshot.resource.resource_id,
        binding_id=snapshot.binding.binding_id,
        version_id=snapshot.version.version_id,
        profile_id="profile-a",
        target_path=target,
        required=required,
    )


def _evidence(gate_id: str) -> UpstreamEvidence:
    return UpstreamEvidence(
        gate_id=gate_id,
        evidence_id=f"evidence-{gate_id}",
        evidence_digest=DIGEST,
        tenant_id=TENANT,
        project_id=PROJECT,
        revision_id="revision-4",
        generation=GENERATION,
    )


class UpstreamPort:
    def __init__(self, evidence: tuple[UpstreamEvidence, ...] | None = None) -> None:
        self.evidence = evidence if evidence is not None else tuple(
            _evidence(gate) for gate in ("MIG-M04-01", "MIG-M04E-01", "MIG-RES-01")
        )
        self.calls = 0

    async def read_upstream_evidence(self, *, request):
        self.calls += 1
        return self.evidence


class SnapshotPort:
    def __init__(self, snapshots: tuple[ResourceSnapshot, ...]) -> None:
        self.snapshots = snapshots
        self.calls = 0

    async def read_snapshots(self, *, request, selections):
        self.calls += 1
        return self.snapshots


class AclPort:
    def __init__(self, *, deny: bool = False) -> None:
        self.deny = deny
        self.calls: list[tuple[str, str]] = []

    async def read_file_ref_acl_evidence(self, *, request, snapshot, asset_id, asset_version_id):
        self.calls.append((asset_id, asset_version_id))
        return FileRefAclEvidence(
            evidence_id=f"acl-{asset_id}",
            evidence_digest=DIGEST,
            tenant_id=TENANT,
            project_id=PROJECT,
            subject_id=SUBJECT,
            resource_id=snapshot.resource.resource_id,
            version_id=snapshot.version.version_id,
            asset_id=asset_id,
            asset_version_id=asset_version_id,
            generation=GENERATION,
            allowed=not self.deny,
        )


class ReadinessPort:
    def __init__(self, *, failed_operation: str | None = None) -> None:
        self.failed_operation = failed_operation
        self.calls: list[str] = []

    async def validate_operation(self, *, request, operation):
        self.calls.append(operation.operation_id)
        failed = operation.operation_id == self.failed_operation
        return ResourceOperationReadiness(
            operation_id=operation.operation_id,
            ready=not failed,
            reason_code="required_dependency_missing" if failed else "ready",
        )


def _builder(
    snapshots: tuple[ResourceSnapshot, ...],
    *,
    gates: tuple[UpstreamEvidence, ...] | None = None,
    deny_acl: bool = False,
    failed_operation: str | None = None,
):
    upstream = UpstreamPort(gates)
    snapshot_port = SnapshotPort(snapshots)
    acl = AclPort(deny=deny_acl)
    readiness = ReadinessPort(failed_operation=failed_operation)
    return ResourcePlanBuilder(ResourcePlanPorts(upstream, snapshot_port, acl, readiness)), upstream, snapshot_port, acl, readiness


def _request(*selections: ResourcePlanSelection) -> ResourcePlanRequest:
    return ResourcePlanRequest(
        tenant_id=TENANT,
        subject_id=SUBJECT,
        project_id=PROJECT,
        revision_id="revision-4",
        target_scope=ResourceScope(ScopeKind.PROJECT, PROJECT, TENANT),
        desired_generation=GENERATION,
        selections=tuple(selections),
    )


@pytest.mark.asyncio
async def test_missing_upstream_m04_and_resource_evidence_blocks_before_snapshot_read():
    snapshot = _snapshot("prompt", ResourceKind.PROMPT, {"body_md": "Instructions"})
    gates = (_evidence("MIG-M04-01"),)
    builder, upstream, snapshots, _, _ = _builder((snapshot,), gates=gates)

    plan = await builder.build(_request(_selection(snapshot)))

    assert not plan.ready
    assert not plan.operations
    assert {failure.code for failure in plan.failures} >= {"upstream_evidence_missing"}
    assert upstream.calls == 1
    assert snapshots.calls == 0


@pytest.mark.asyncio
async def test_plan_is_deterministic_and_pins_profile_version_asset_digest_and_prompt_priority():
    low = _snapshot("prompt-low", ResourceKind.PROMPT, {"body_md": "Low"}, priority=2)
    high = _snapshot("prompt-high", ResourceKind.PROMPT, {"body_md": "High"}, priority=8)
    file_payload = {
        "file_ref": {
            "asset_id": "asset-a",
            "version_id": "asset-v3",
            "storage_key": "blob/immutable-a",
            "filename": "readme.md",
            "sha256": DIGEST,
        },
        "asset_digest": DIGEST,
    }
    file_snapshot = _snapshot("file-a", ResourceKind.FILE, file_payload)
    selections = (_selection(low), _selection(file_snapshot), _selection(high))
    builder, _, _, acl, readiness = _builder((high, file_snapshot, low))

    first = await builder.build(_request(*selections))
    second = await builder.build(_request(*reversed(selections)))

    assert first.ready and second.ready
    assert first.revision_digest == second.revision_digest
    assert first.snapshot_digest == second.snapshot_digest
    assert [item.resource_id for item in first.prompt_order] == ["prompt-high", "prompt-low"]
    file_operation = next(item for item in first.operations if item.resource_id == "file-a")
    assert file_operation.profile_id == "profile-a"
    assert file_operation.version_id == "file-a-v1"
    assert file_operation.version_digest == file_snapshot.version.content_digest
    assert file_operation.asset_id == "asset-a"
    assert file_operation.asset_version_id == "asset-v3"
    assert file_operation.asset_digest == DIGEST
    assert file_operation.source_kind == "legacy_resource"
    assert file_operation.source_id == "source-file-a"
    assert file_operation.source_digest == DIGEST
    assert file_operation.scope_proof_id == "scope-proof-file-a"
    assert file_operation.acl_evidence_id == "acl-asset-a"
    assert file_operation.acl_evidence_digest == DIGEST
    assert acl.calls == [("asset-a", "asset-v3"), ("asset-a", "asset-v3")]
    assert readiness.calls


@pytest.mark.asyncio
async def test_null_project_selection_is_denied_as_ambiguous():
    snapshot = _snapshot("prompt", ResourceKind.PROMPT, {"body_md": "Instructions"})
    binding = replace(snapshot.binding, selection_policy=None, project_ids=None)
    malformed = replace(snapshot, binding=binding)
    builder, _, _, _, _ = _builder((malformed,))

    plan = await builder.build(_request(_selection(snapshot)))

    assert not plan.ready
    assert not plan.operations
    assert "ambiguous_project_selection" in {failure.code for failure in plan.failures}


@pytest.mark.asyncio
async def test_file_ref_without_acl_evidence_blocks_plan():
    file_payload = {
        "file_ref": {
            "asset_id": "asset-a",
            "version_id": "asset-v3",
            "storage_key": "blob/immutable-a",
            "filename": "readme.md",
            "sha256": DIGEST,
        },
        "asset_digest": DIGEST,
    }
    snapshot = _snapshot("file-a", ResourceKind.FILE, file_payload)

    class MissingAcl:
        async def read_file_ref_acl_evidence(self, **kwargs):
            return None

    ports = ResourcePlanPorts(UpstreamPort(), SnapshotPort((snapshot,)), MissingAcl(), ReadinessPort())
    plan = await ResourcePlanBuilder(ports).build(_request(_selection(snapshot)))

    assert not plan.ready
    assert not plan.operations
    assert "file_ref_acl_evidence_missing_or_mismatched" in {failure.code for failure in plan.failures}


@pytest.mark.asyncio
async def test_denied_file_ref_acl_evidence_blocks_plan():
    payload = {
        "file_ref": {
            "asset_id": "asset-denied",
            "version_id": "asset-denied-v1",
            "storage_key": "blob/denied",
            "filename": "denied.md",
            "sha256": DIGEST,
        },
        "asset_digest": DIGEST,
    }
    snapshot = _snapshot("file-denied", ResourceKind.FILE, payload)
    builder, _, _, _, _ = _builder((snapshot,), deny_acl=True)

    plan = await builder.build(_request(_selection(snapshot)))

    assert not plan.ready and not plan.operations
    assert "file_ref_acl_evidence_missing_or_mismatched" in {failure.code for failure in plan.failures}


@pytest.mark.asyncio
async def test_path_collision_and_parent_child_conflicts_block_without_partial_operations():
    first = _snapshot("prompt-a", ResourceKind.PROMPT, {"body_md": "A"})
    second = _snapshot("prompt-b", ResourceKind.PROMPT, {"body_md": "B"})
    builder, _, _, _, _ = _builder((first, second))

    duplicate = await builder.build(_request(_selection(first, target="docs/shared.md"), _selection(second, target="docs/shared.md")))
    parent_child = await builder.build(_request(_selection(first, target="assets"), _selection(second, target="assets/data.csv")))

    assert not duplicate.ready and not duplicate.operations
    assert "target_path_conflict" in {failure.code for failure in duplicate.failures}
    assert not parent_child.ready and not parent_child.operations
    assert "target_path_conflict" in {failure.code for failure in parent_child.failures}


@pytest.mark.asyncio
async def test_mcp_manifest_must_validate_before_readiness_barrier():
    invalid = _snapshot("mcp-a", ResourceKind.MCP, {
        "file_ref": {
            "asset_id": "asset-mcp",
            "version_id": "asset-mcp-v1",
            "storage_key": "blob/mcp",
            "filename": "mcp.json",
            "sha256": DIGEST,
        },
        "manifest": {"mcpServers": {"server": {"url": "file:///etc/passwd"}}},
    })
    builder, _, _, _, readiness = _builder((invalid,))

    plan = await builder.build(_request(_selection(invalid)))

    assert not plan.ready and not plan.operations
    assert "unsupported_mcp_manifest" in {failure.code for failure in plan.failures}
    assert readiness.calls == []


@pytest.mark.asyncio
async def test_optional_readiness_failure_still_blocks_and_clears_entire_plan():
    optional = _snapshot("prompt-optional", ResourceKind.PROMPT, {"body_md": "Maybe"})
    required = _snapshot("prompt-required", ResourceKind.PROMPT, {"body_md": "Must"})
    failed_id = f"resource:{optional.resource.resource_id}:{optional.binding.binding_id}:{optional.version.version_id}"
    builder, _, _, _, readiness = _builder((optional, required), failed_operation=failed_id)

    plan = await builder.build(_request(_selection(optional, required=False), _selection(required)))

    assert not plan.ready
    assert not plan.operations
    assert any(failure.code == "required_dependency_missing" and not failure.required for failure in plan.failures)
    assert len(readiness.calls) == 2


@pytest.mark.asyncio
async def test_required_operation_readiness_failure_blocks_and_clears_entire_plan():
    required = _snapshot("prompt-required", ResourceKind.PROMPT, {"body_md": "Must"})
    optional = _snapshot("prompt-optional", ResourceKind.PROMPT, {"body_md": "Maybe"})
    failed_id = f"resource:{required.resource.resource_id}:{required.binding.binding_id}:{required.version.version_id}"
    builder, _, _, _, _ = _builder((required, optional), failed_operation=failed_id)

    plan = await builder.build(_request(_selection(required), _selection(optional, required=False)))

    assert not plan.ready
    assert not plan.operations
    assert any(failure.code == "required_dependency_missing" for failure in plan.failures)


@pytest.mark.asyncio
async def test_legacy_non_materializable_resource_kind_is_denied():
    legacy = _snapshot("connection-a", ResourceKind.CONNECTION, {"endpoint": "https://service.invalid"})
    builder, _, _, _, _ = _builder((legacy,))

    plan = await builder.build(_request(_selection(legacy)))

    assert not plan.ready and not plan.operations
    assert "resource_kind_unsupported" in {failure.code for failure in plan.failures}


@pytest.mark.asyncio
async def test_unbound_snapshot_and_revision_mismatch_are_denied():
    snapshot = _snapshot("prompt", ResourceKind.PROMPT, {"body_md": "Instructions"})
    assert snapshot.provenance is not None
    foreign = replace(
        snapshot,
        scope_proof=replace(snapshot.scope_proof, generation=GENERATION + 1),
        generation=GENERATION + 1,
        provenance=replace(snapshot.provenance, generation=GENERATION + 1),
    )
    builder, _, _, _, _ = _builder((foreign,))

    plan = await builder.build(_request(_selection(snapshot)))

    assert not plan.ready and not plan.operations
    assert "scope_proof_mismatch" in {failure.code for failure in plan.failures}
