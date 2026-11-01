"""Contract tests for persisted materialization plan types."""

from dataclasses import FrozenInstanceError, replace

import pytest

from prodavan.application.materialization.identity import (
    build_digest,
    build_idempotency_key,
)
from prodavan.domain.materialization.types import (
    FailureDiagnostic,
    MaterializationOperation,
    MaterializationPlanEntry,
    MaterializationStatus,
    OperationKind,
)
from tests.fixtures.materialization import MATERIALIZATION_SOURCE, make_materialization_entry


def test_plan_entry_is_immutable_and_defaults_to_planned() -> None:
    entry = make_materialization_entry()
    assert entry.status is MaterializationStatus.PLANNED
    assert entry.applied_generation is None
    with pytest.raises(FrozenInstanceError):
        entry.status = MaterializationStatus.APPLIED  # type: ignore[misc]


def test_plan_entry_supports_all_operation_kinds() -> None:
    for kind in OperationKind:
        operation = MaterializationOperation(kind=kind, target=f"target/{kind.value}")
        assert operation.kind is kind


def test_failed_entry_requires_structured_diagnostics() -> None:
    entry = make_materialization_entry()
    with pytest.raises(ValueError, match="failed entry"):
        replace(entry, status=MaterializationStatus.FAILED)

    failed = replace(
        entry,
        status=MaterializationStatus.FAILED,
        failure=FailureDiagnostic(
            code="workspace_unavailable", message="workspace writer unavailable", retryable=True
        ),
    )
    assert failed.failure is not None
    assert failed.failure.retryable


def test_applied_generation_and_idempotent_state() -> None:
    entry = make_materialization_entry()
    applied = replace(entry, status=MaterializationStatus.APPLIED, applied_generation=7)
    assert applied.is_idempotently_applied
    with pytest.raises(ValueError, match="cannot exceed"):
        replace(entry, status=MaterializationStatus.APPLIED, applied_generation=8)


def test_digest_and_idempotency_are_stable_for_mapping_order() -> None:
    first = make_materialization_entry()
    second_operation = MaterializationOperation(
        kind=OperationKind.PROMPT,
        target=first.operation.target,
        payload={"priority": 100, "body": "Use explicit tool scopes."},
    )
    second_digest = build_digest(
        project_id=first.project_id,
        operation=second_operation,
        provenance=MATERIALIZATION_SOURCE,
        desired_generation=7,
    )
    first_digest = build_digest(
        project_id=first.project_id,
        operation=first.operation,
        provenance=MATERIALIZATION_SOURCE,
        desired_generation=7,
    )
    assert first_digest == second_digest
    assert build_idempotency_key(project_id=first.project_id, target=first.operation.target, digest=first_digest) == build_idempotency_key(
        project_id=first.project_id, target=first.operation.target, digest=second_digest
    )
    assert first_digest != build_digest(
        project_id=first.project_id,
        operation=first.operation,
        provenance=MATERIALIZATION_SOURCE,
        desired_generation=8,
    )
