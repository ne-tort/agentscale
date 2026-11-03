"""MIG-RES-01 lifecycle acceptance tests; intentionally not executed."""
from __future__ import annotations

from dataclasses import replace

import pytest

from prodavan.application.migration.resources import (
    AssetReference,
    Binding,
    BindingState,
    ImportOutcome,
    ProjectSelectionPolicy,
    ProfileCandidate,
    Resource,
    ResourceImportContext,
    ResourceImportCoordinator,
    ResourceImportPartition,
    ResourceKind,
    ResourceLifecycle,
    ResourceScope,
    ResourceVersion,
    ScopeKind,
    VersionStatus,
    canonical_digest,
    import_compatibility_candidate,
    resolve_profile,
    resolve_resource,
    select_deterministic_profile,
    validate_candidate,
    validate_resource_payload,
)
from lifecycle_acceptance_fixtures import (
    ASSET_DIGEST,
    PROJECT_A,
    PROJECT_B,
    SNAPSHOT_DIGEST,
    SUBJECT_A,
    TENANT_A,
    TENANT_B,
    AtomicImportMemory,
    AuthorizedScope,
    PageSource,
    ResumableImportMemory,
    ScopeMemory,
    SnapshotMemory,
    file_candidate,
    file_payload,
    file_resource,
    project_binding,
    project_snapshot,
)


@pytest.mark.asyncio
async def test_file_asset_digest_is_immutable_and_provenance_ready() -> None:
    resource = file_resource()
    version = resource.versions[0]
    asset = AssetReference.from_mapping(version.payload["file_ref"])

    assert asset.digest == ASSET_DIGEST
    assert version.asset_digest == ASSET_DIGEST
    assert resource.source_digest.startswith("sha256:")
    with pytest.raises(TypeError):
        version.payload["path"] = "changed.md"


@pytest.mark.asyncio
async def test_asset_digest_conflict_is_quarantined_before_import_port() -> None:
    payload = file_payload()
    payload["asset_digest"] = "sha256:" + "3" * 64
    resource = file_resource(payload=payload)
    candidate = file_candidate()
    candidate = replace(
        candidate,
        resource=resource,
        binding=project_binding(resource),
        digest="",
    )
    from prodavan.application.migration.resources import candidate_canonical_digest
    candidate = replace(candidate, digest=candidate_canonical_digest(candidate))
    store = AtomicImportMemory()

    result = await import_compatibility_candidate(
        store=store,
        candidate=candidate,
        dependencies_ready=True,
    )

    assert result.outcome is ImportOutcome.QUARANTINED
    assert result.reason == "asset_digest_conflict"
    assert store.calls == []


@pytest.mark.asyncio
async def test_cross_tenant_import_is_quarantined_before_scope_read() -> None:
    store = AtomicImportMemory()
    scope = ScopeMemory()
    result = await import_compatibility_candidate(
        store=store,
        candidate=file_candidate(tenant_id=TENANT_B, project_id=PROJECT_B),
        dependencies_ready=True,
        import_context=ResourceImportContext(
            tenant_id=TENANT_A,
            m04_scope_ready=True,
            scope_port=scope,
            correlation_id="corr-cross-tenant",
        ),
    )

    assert result.outcome is ImportOutcome.QUARANTINED
    assert result.reason == "cross_tenant_resource"
    assert scope.calls == 0
    assert store.calls == []


@pytest.mark.asyncio
async def test_import_replay_is_idempotent_and_changed_digest_conflicts() -> None:
    store = AtomicImportMemory()
    original = file_candidate(source_id="source-replay")
    first = await import_compatibility_candidate(store=store, candidate=original, dependencies_ready=True)
    replay = await import_compatibility_candidate(store=store, candidate=original, dependencies_ready=True)

    changed_resource = file_resource(payload=file_payload(path="docs/changed.md"))
    changed = replace(
        original,
        resource=changed_resource,
        binding=project_binding(changed_resource),
        digest="",
    )
    from prodavan.application.migration.resources import candidate_canonical_digest
    changed = replace(changed, digest=candidate_canonical_digest(changed))
    conflict = await import_compatibility_candidate(store=store, candidate=changed, dependencies_ready=True)

    assert first.outcome is ImportOutcome.IMPORTED
    assert replay.outcome is ImportOutcome.REPLAY
    assert conflict.outcome is ImportOutcome.CONFLICT
    assert conflict.reason == "source_digest_changed"
    assert store.records[("legacy_file", "source-replay", "resource-v1")].digest == first.digest
    assert len(store.conflicts) == 1


@pytest.mark.asyncio
async def test_resumable_import_advances_checkpoint_and_replays_completed_partition() -> None:
    candidates = (file_candidate(source_id="source-a"), file_candidate(source_id="source-b"))
    source = PageSource(candidates)
    store = ResumableImportMemory()
    partition = ResourceImportPartition(
        partition_id="tenant-a:resources",
        tenant_id=TENANT_A,
        source_snapshot_id="snapshot-resources-1",
        source_schema_version="resource-v1",
        source_snapshot_digest=SNAPSHOT_DIGEST,
    )
    coordinator = ResourceImportCoordinator(
        source=source,
        store=store,
        page_size=1,
        dependencies_ready=True,
    )

    first = await coordinator.run_page(partition=partition, correlation_id="corr-resume")
    second = await coordinator.run_page(partition=partition, correlation_id="corr-resume")
    completed = await coordinator.run_page(partition=partition, correlation_id="corr-resume")
    replay = await coordinator.run_page(partition=partition, correlation_id="corr-resume")

    assert first.complete is False
    assert first.checkpoint.sequence == 1
    assert second.complete is False
    assert second.checkpoint.sequence == 2
    assert completed.complete is True
    assert completed.checkpoint.sequence == 2
    assert replay.complete is True
    assert replay.reason == "already_complete"
    assert set(store.records) == {
        ("legacy_file", "source-a", "resource-v1"),
        ("legacy_file", "source-b", "resource-v1"),
    }


def test_profile_selection_is_deterministic_and_explicit_binding_wins() -> None:
    explicit = ProfileCandidate(
        profile_id="profile-z",
        resource_id="resource-z",
        binding_id="binding-z",
        tenant_id=TENANT_A,
        project_ids=(PROJECT_A,),
        selection_policy=ProjectSelectionPolicy.EXPLICIT_PROJECTS,
        priority=1,
        version_number=2,
    )
    all_bound = ProfileCandidate(
        profile_id="profile-a",
        resource_id="resource-a",
        binding_id="binding-a",
        tenant_id=TENANT_A,
        project_ids=None,
        bound_project_ids=(PROJECT_A,),
        selection_policy=ProjectSelectionPolicy.ALL_BOUND_PROJECTS,
        priority=100,
        version_number=9,
    )

    one = select_deterministic_profile((all_bound, explicit), tenant_id=TENANT_A, project_id=PROJECT_A)
    two = select_deterministic_profile((explicit, all_bound), tenant_id=TENANT_A, project_id=PROJECT_A)

    assert one.resolved and two.resolved
    assert one.selected is explicit
    assert two.selected is explicit
    assert one.candidates_digest == two.candidates_digest


def test_null_project_selection_policy_is_ambiguous_and_denied() -> None:
    candidate = ProfileCandidate(
        profile_id="profile-ambiguous",
        resource_id="resource-ambiguous",
        binding_id="binding-ambiguous",
        tenant_id=TENANT_A,
        project_ids=None,
        selection_policy=None,
        priority=1,
        version_number=1,
    )

    result = select_deterministic_profile((candidate,), tenant_id=TENANT_A, project_id=PROJECT_A)

    assert not result.resolved
    assert result.reason == "ambiguous_project_selection"


@pytest.mark.asyncio
async def test_read_only_resolution_returns_exact_asset_and_scope_provenance() -> None:
    snapshot = project_snapshot()
    store = SnapshotMemory(snapshot)

    result = await resolve_resource(
        store=store,
        scope_resolver=AuthorizedScope(),
        m04_scope_ready=True,
        tenant_id=TENANT_A,
        subject_id=SUBJECT_A,
        session_id=None,
        project_id=PROJECT_A,
        binding_id=snapshot.binding.binding_id,
        resource_id=snapshot.resource.resource_id,
        version_id=snapshot.version.version_id,
        profile_id="profile-primary",
    )

    assert result.resolved
    assert result.provenance is not None
    assert result.provenance.asset_digest == ASSET_DIGEST
    assert result.provenance.scope_proof_id == snapshot.scope_proof.proof_id
    assert len(store.calls) == 1


@pytest.mark.asyncio
async def test_snapshot_from_another_tenant_is_denied_even_when_port_returns_it() -> None:
    snapshot = project_snapshot(tenant_id=TENANT_A, project_id=PROJECT_A)
    result = await resolve_resource(
        store=SnapshotMemory(snapshot),
        scope_resolver=AuthorizedScope(),
        m04_scope_ready=True,
        tenant_id=TENANT_B,
        subject_id=SUBJECT_A,
        session_id=None,
        project_id=PROJECT_B,
        binding_id=snapshot.binding.binding_id,
        resource_id=snapshot.resource.resource_id,
        version_id=snapshot.version.version_id,
    )

    assert not result.resolved
    assert result.reason == "immutable_snapshot_identity_or_lifecycle_mismatch"


def test_mcp_manifest_with_literal_authorization_is_quarantined() -> None:
    payload = file_payload()
    payload["manifest"] = {
        "mcpServers": {
            "tools": {
                "url": "https://mcp.invalid/tools",
                "headers": {"Authorization": "Bearer raw-token"},
            }
        }
    }
    assert "unsupported_mcp_manifest" in validate_resource_payload(ResourceKind.MCP, payload)


def test_append_only_publish_keeps_old_version_and_pins_new_version() -> None:
    resource = file_resource()
    second_payload = file_payload(path="docs/v2.md")
    second = ResourceVersion(
        version_id="resource-file-v2",
        sequence=2,
        schema_version="resource-v1",
        content_digest=canonical_digest(second_payload),
        payload=second_payload,
        resource_id=resource.resource_id,
        status=VersionStatus.DRAFT,
    )

    appended = resource.with_appended_version(second)
    published = appended.publish_version(second.version_id)

    assert appended.versions[0].payload["path"] == "docs/readme.md"
    assert appended.active_version == resource.active_version
    assert published.active_version == 2
    assert published.versions[0].status is VersionStatus.RETIRED
    assert published.versions[1].status is VersionStatus.PUBLISHED


def test_duplicate_project_binding_is_quarantined() -> None:
    resource = file_resource()
    binding = replace(project_binding(resource), project_ids=(PROJECT_A, PROJECT_A))
    candidate = file_candidate()
    candidate = replace(candidate, binding=binding, digest="")
    from prodavan.application.migration.resources import candidate_canonical_digest
    candidate = replace(candidate, digest=candidate_canonical_digest(candidate))

    reasons = validate_candidate(candidate)

    assert "duplicate_project_binding" in reasons


@pytest.mark.asyncio
async def test_archived_resource_and_disabled_binding_are_denied_read_only() -> None:
    snapshot = project_snapshot()
    archived = replace(snapshot, resource=snapshot.resource.archive())
    disabled = replace(snapshot, binding=replace(snapshot.binding, state=BindingState.DISABLED))

    archived_result = await resolve_resource(
        store=SnapshotMemory(archived),
        scope_resolver=AuthorizedScope(),
        m04_scope_ready=True,
        tenant_id=TENANT_A,
        subject_id=SUBJECT_A,
        session_id=None,
        project_id=PROJECT_A,
        binding_id=snapshot.binding.binding_id,
        resource_id=snapshot.resource.resource_id,
        version_id=snapshot.version.version_id,
    )
    disabled_result = await resolve_resource(
        store=SnapshotMemory(disabled),
        scope_resolver=AuthorizedScope(),
        m04_scope_ready=True,
        tenant_id=TENANT_A,
        subject_id=SUBJECT_A,
        session_id=None,
        project_id=PROJECT_A,
        binding_id=snapshot.binding.binding_id,
        resource_id=snapshot.resource.resource_id,
        version_id=snapshot.version.version_id,
    )

    assert not archived_result.resolved
    assert not disabled_result.resolved
