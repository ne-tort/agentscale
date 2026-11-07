"""Source-level contract tests for the exclusive materialization adapter zone."""

from dataclasses import FrozenInstanceError
from inspect import getsource

import pytest

from prodavan.application.materialization_adapter import service

from prodavan.application.materialization_adapter import (
    ResourceMaterializationAdapter,
    ResourceMaterializationRequest,
    ResourceOperation,
)
from prodavan.domain.materialization import OperationKind, SourceProvenance


SOURCE = SourceProvenance(
    source_type="resource",
    source_id="module-row-1",
    source_generation=12,
    source_path="modules/prompts/row-1",
    source_revision="rev-12",
    metadata={"module_id": "mod_management", "asset_id": "asset-1"},
)


def _request(*, applied_generation: int | None = None, operations=None):
    return ResourceMaterializationRequest(
        project_id="project-1",
        revision_id="revision-12",
        desired_generation=12,
        applied_generation=applied_generation,
        source=SOURCE,
        required_kinds=frozenset(OperationKind),
        operations=tuple(operations or []),
    )


def test_maps_all_resource_operation_kinds_with_provenance() -> None:
    operations = tuple(
        ResourceOperation(kind=kind, target=f"workspace/{kind.value}", payload={"value": kind.value})
        for kind in OperationKind
    )

    plan = ResourceMaterializationAdapter().build_plan(_request(operations=operations))

    assert plan.is_complete
    assert {entry.kind for entry in plan.entries} == {kind.value for kind in OperationKind}
    assert all(entry.provenance["source_id"] == "module-row-1" for entry in plan.entries)
    assert all(entry.generation == 12 for entry in plan.entries)
    assert plan.desired_applied_generation == (12, None)


def test_missing_required_operation_fails_closed_without_partial_success() -> None:
    operations = tuple(
        ResourceOperation(kind=kind, target=f"workspace/{kind.value}")
        for kind in (OperationKind.PROMPT, OperationKind.FILE)
    )

    plan = ResourceMaterializationAdapter().build_plan(_request(operations=operations))

    assert not plan.is_complete
    assert set(plan.missing_required) == {
        OperationKind.MCP,
        OperationKind.ENV,
        OperationKind.SCOPE,
    }
    assert plan.diagnostics["fail_closed"] is True


def test_digest_and_idempotency_are_order_stable_and_generation_sensitive() -> None:
    operations = (
        ResourceOperation(kind=OperationKind.PROMPT, target="AGENTS.md", payload={"b": 2, "a": 1}),
        ResourceOperation(kind=OperationKind.ENV, target=".env", payload={"MODEL": "x"}),
    )
    reordered = tuple(reversed(operations))
    adapter = ResourceMaterializationAdapter()

    first = adapter.build_plan(_request(operations=operations))
    second = adapter.build_plan(_request(operations=reordered))
    next_generation = adapter.build_plan(
        ResourceMaterializationRequest(
            project_id="project-1",
            revision_id="revision-13",
            desired_generation=13,
            source=SOURCE,
            operations=operations,
        )
    )

    assert first.digest == second.digest
    assert first.idempotency_key == second.idempotency_key
    assert first.digest != next_generation.digest


def test_plan_and_nested_payloads_are_immutable() -> None:
    operation = ResourceOperation(kind=OperationKind.FILE, target="README.md", payload={"parts": ["a"]})
    plan = ResourceMaterializationAdapter().build_plan(_request(operations=(operation,)))

    with pytest.raises(FrozenInstanceError):
        plan.project_id = "other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        plan.entries[0].provenance["source_id"] = "other"  # type: ignore[index]


def test_duplicate_workspace_targets_fail_closed_for_persistence_map() -> None:
    operation = ResourceOperation(kind=OperationKind.FILE, target="README.md", payload={"value": "x"})
    duplicate = ResourceOperation(kind=OperationKind.PROMPT, target="README.md", payload={"value": "y"})

    plan = ResourceMaterializationAdapter().build_plan(_request(operations=(operation, duplicate)))

    assert not plan.is_complete
    assert plan.diagnostics["duplicate_targets"] == ("README.md",)
    assert plan.diagnostics["fail_closed"] is True


def test_operation_identity_includes_generation_and_provenance() -> None:
    operation = ResourceOperation(kind=OperationKind.FILE, target="README.md", payload={"value": "x"})
    adapter = ResourceMaterializationAdapter()
    changed_source = SourceProvenance(
        source_type=SOURCE.source_type,
        source_id=SOURCE.source_id,
        source_generation=SOURCE.source_generation + 1,
        source_path=SOURCE.source_path,
        source_revision=SOURCE.source_revision,
        metadata=SOURCE.metadata,
    )

    first = adapter.build_plan(_request(operations=(operation,)))
    changed_source_plan = adapter.build_plan(
        ResourceMaterializationRequest(
            project_id="project-1",
            revision_id="revision-12",
            desired_generation=12,
            source=changed_source,
            operations=(operation,),
        )
    )

    assert first.entries[0].operation_digest != changed_source_plan.entries[0].operation_digest
    assert first.entries[0].idempotency_key != changed_source_plan.entries[0].idempotency_key


def test_adapter_is_planner_only_and_does_not_call_legacy_executor() -> None:
    source = getsource(service)

    assert "MaterializeExecutor" not in source
    assert "execute(" not in source


def test_caller_supplied_operations_are_the_only_operation_boundary() -> None:
    operation = ResourceOperation(kind=OperationKind.PROMPT, target="AGENTS.md")
    plan = ResourceMaterializationAdapter().build_plan(_request(operations=(operation,)))

    assert len(plan.entries) == 1
    assert plan.entries[0].kind == OperationKind.PROMPT.value
    assert plan.entries[0].workspace_path == "AGENTS.md"


def test_applied_generation_is_preserved_as_projection() -> None:
    plan = ResourceMaterializationAdapter().build_plan(
        _request(
            applied_generation=11,
            operations=(ResourceOperation(kind=OperationKind.SCOPE, target="scope/project-1"),),
        )
    )

    assert plan.desired_generation == 12
    assert plan.applied_generation == 11
    assert plan.desired_applied_generation == (12, 11)
