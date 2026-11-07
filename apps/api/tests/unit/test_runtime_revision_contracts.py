"""Unit tests for runtime revision ORM and application contracts.

These tests are intentionally isolated from a database: they verify the public
shape, invariants, and metadata that adapters must preserve.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from sqlalchemy import UniqueConstraint

from prodavan.application.runtime_revision import (
    CapabilityBundle,
    MaterializationEntry,
    MaterializationStatus,
    ProjectRuntimeRevision,
    RevisionState,
)
from prodavan.infrastructure.persistence.models.runtime_revisions import (
    CapabilityBundleRow,
    MaterializationEntryRow,
    ProjectRuntimeRevisionRow,
)


def test_runtime_tables_have_expected_names_and_revision_uniqueness() -> None:
    assert CapabilityBundleRow.__tablename__ == "capability_bundles"
    assert ProjectRuntimeRevisionRow.__tablename__ == "project_runtime_revisions"
    assert MaterializationEntryRow.__tablename__ == "materialization_entries"

    revision_constraints = {
        tuple(constraint.columns.keys())
        for constraint in ProjectRuntimeRevisionRow.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    entry_constraints = {
        tuple(constraint.columns.keys())
        for constraint in MaterializationEntryRow.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("project_id", "revision") in revision_constraints
    assert ("revision_id", "workspace_path") in entry_constraints


def test_revision_defaults_are_planned_and_entries_keep_provenance() -> None:
    bundle = CapabilityBundle(
        bundle_id="sha256:bundle",
        project_id="proj_1",
        revision=3,
        manifest={"modules": []},
        digest="sha256:bundle",
    )
    revision = ProjectRuntimeRevision(
        revision_id="rr_1",
        project_id="proj_1",
        revision=3,
        bundle=bundle,
    )
    entry = MaterializationEntry(
        entry_id="me_1",
        revision_id=revision.revision_id,
        workspace_path="/workspace/prompts/system.md",
        kind="prompt",
        generation=4,
        source_capability_id="mod_management",
        provenance={"order": 10},
    )

    assert revision.state is RevisionState.PLANNED
    assert revision.hydrate_generation == 0
    assert entry.status is MaterializationStatus.PLANNED
    assert entry.provenance == {"order": 10}


def test_contract_snapshots_are_immutable() -> None:
    bundle = CapabilityBundle("b", "p", 1, {}, "d")
    revision = ProjectRuntimeRevision("r", "p", 1, bundle)
    entry = MaterializationEntry("e", "r", "/x", "file", 0)

    with pytest.raises(FrozenInstanceError):
        revision.state = RevisionState.READY  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        entry.status = MaterializationStatus.APPLIED  # type: ignore[misc]


def test_lifecycle_states_cover_prepare_and_apply_outcomes() -> None:
    assert {state.value for state in RevisionState} == {
        "planned",
        "validated",
        "applying",
        "ready",
        "failed",
        "superseded",
    }
    assert {status.value for status in MaterializationStatus} == {
        "planned",
        "staged",
        "applied",
        "failed",
    }
