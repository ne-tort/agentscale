"""Source-level contracts for the exclusive resource resolver zone."""

from datetime import UTC, datetime
from inspect import getsource

from prodavan.application.resource_materialization import (
    ResourceMaterializationContext,
    ResourceMaterializationResolver,
    ResourceSelection,
)
from prodavan.application.resource_materialization import contracts, service
from prodavan.application.resources import (
    BindingKind,
    ContentResource,
    ResourceBinding,
    ResourceKind,
    ResourceScope,
    ResourceVersion,
    ScopeKind,
    VersionStatus,
)


def _resource(kind=ResourceKind.FILE, active_version=2):
    return ContentResource(
        id="res-1", kind=kind, slug="readme", title="README",
        scope=ResourceScope(ScopeKind.PROJECT, "project-1"),
        metadata={"path": "README.md"}, active_version=active_version,
    )


def _version(number=2):
    return ResourceVersion(
        id="version-2", resource_id="res-1", number=number,
        status=VersionStatus.PUBLISHED, payload={"content": "hello"},
        created_at=datetime.now(UTC),
    )


def _binding(version=2, binding_id="bind-1"):
    return ResourceBinding(
        id=binding_id, resource_id="res-1",
        target=ResourceScope(ScopeKind.PROJECT, "project-1"),
        binding=BindingKind.LOCAL, child_may_edit=False,
        version_number=version,
    )


def test_zone_is_exclusive_and_does_not_reimplement_adapter():
    source = getsource(service)
    assert "MaterializeExecutor" not in source
    assert "materialization_adapter.service" not in source
    assert "ResourceMaterializationAdapter" not in source
    assert "class ResourceMaterializationResolver" in source


def test_contracts_are_immutable_and_resolution_requires_complete_state():
    source = getsource(contracts)
    assert "@dataclass(frozen=True, slots=True)" in source
    assert "cannot materialize an incomplete resource resolution" in source
    assert "ResourceMaterializationRequest" in source


def test_explicit_selection_contract_contains_scope_binding_and_version():
    selection = ResourceSelection(
        resource_id="res-1", binding_id="bind-1",
        scope=ResourceScope(ScopeKind.PROJECT, "project-1"), version_number=2,
    )
    assert selection.scope.id == "project-1"
    assert selection.binding_id == "bind-1"
    assert selection.version_number == 2


def test_service_has_fail_closed_guards_for_ambiguity_staleness_and_required_ops():
    source = getsource(service)
    for marker in (
        "binding_ambiguous", "binding_stale", "resource_stale",
        "version_not_published", "target_invalid",
        "if any(failure.required for failure in failures)",
    ):
        assert marker in source


def test_service_canonicalizes_request_and_validates_workspace_targets():
    source = getsource(service)
    assert "sorted(" in source
    assert "normalize_workspace_path" in source
    assert "resource_scope_mismatch" in source


def test_context_preserves_generation_projection():
    context = ResourceMaterializationContext(
        project_id="project-1", revision_id="revision-2",
        target_scope=ResourceScope(ScopeKind.PROJECT, "project-1"),
        desired_generation=2, applied_generation=1,
    )
    assert context.desired_generation == 2
    assert context.applied_generation == 1
