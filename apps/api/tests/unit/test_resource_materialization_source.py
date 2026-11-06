"""Source-level contracts for the exclusive resource resolver zone."""

from datetime import UTC, datetime
from inspect import getsource

import pytest

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


def _version(number=2, resource_id="res-1", version_id="version-2"):
    return ResourceVersion(
        id=version_id, resource_id=resource_id, number=number,
        status=VersionStatus.PUBLISHED, payload={"content": "hello"},
        created_at=datetime.now(UTC),
    )


def _binding(version=2, binding_id="bind-1", resource_id="res-1"):
    return ResourceBinding(
        id=binding_id, resource_id=resource_id,
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
    # The barrier is *not* a ``failure.required`` filter: such a filter would let
    # an optional failure leave a partial operation set behind, which is exactly
    # the partial-apply hazard the resolver exists to prevent (see
    # docs/08-migration/implementation-static-risk-review.md — "optional failure
    # оставляет частичный набор операций"). The contract is stronger: any failure,
    # required or optional, clears the whole set. The previous expectation of
    # ``if any(failure.required for failure in failures)`` described a weaker
    # barrier that production deliberately never implemented; the behavioural
    # half of that rule is pinned by
    # ``test_optional_selection_failure_invalidates_the_complete_set`` below.
    for marker in (
        "binding_ambiguous", "binding_stale", "resource_stale",
        "version_not_published", "target_invalid",
        "if failures:",
        "operations = []",
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


class _StubSource:
    """Explicit source port: nothing is inferred from a latest/default lookup."""

    def __init__(self, *, resources, versions, bindings):
        self._resources = {resource.id: resource for resource in resources}
        self._versions = {(item.resource_id, item.number): item for item in versions}
        self._bindings = tuple(bindings)

    async def get_resource(self, resource_id):
        return self._resources.get(resource_id)

    async def get_version(self, resource_id, number):
        return self._versions.get((resource_id, number))

    async def list_bindings(self, target):
        return list(self._bindings)


@pytest.mark.asyncio
async def test_optional_selection_failure_invalidates_the_complete_set():
    """An optional failure must not leave a partial operation set to apply.

    The resolver is the fail-closed gate in front of the atomic apply port: a
    plan built from a *partially* resolved selection would materialize a state
    the caller never asked for, so optional and required failures block alike.
    """
    healthy_optional = _resource()
    healthy_required = ContentResource(
        id="res-2", kind=ResourceKind.PROMPT, slug="rules", title="Rules",
        scope=ResourceScope(ScopeKind.PROJECT, "project-1"),
        metadata={"path": "prompts/rules.md"}, active_version=1,
    )
    resolver = ResourceMaterializationResolver(
        source=_StubSource(
            resources=(healthy_optional, healthy_required),
            versions=(
                _version(),
                _version(number=1, resource_id="res-2", version_id="version-2b"),
            ),
            bindings=(
                _binding(),
                _binding(version=1, binding_id="bind-2", resource_id="res-2"),
            ),
        )
    )
    scope = ResourceScope(ScopeKind.PROJECT, "project-1")
    context = ResourceMaterializationContext(
        project_id="project-1",
        revision_id="revision-2",
        target_scope=scope,
        desired_generation=2,
        selections=(
            # Optional, but stale: the binding sits on version 2 while the
            # selection asks for version 1.
            ResourceSelection(
                resource_id="res-1", binding_id="bind-1", scope=scope,
                version_number=1, required=False,
            ),
            ResourceSelection(
                resource_id="res-2", binding_id="bind-2", scope=scope,
                version_number=1, required=True,
            ),
        ),
    )

    resolution = await resolver.resolve(context)

    assert [failure.code for failure in resolution.failures] == ["binding_stale"]
    assert resolution.failures[0].required is False
    assert resolution.is_complete is False
    assert resolution.operations == ()
    with pytest.raises(ValueError):
        resolution.to_request()
