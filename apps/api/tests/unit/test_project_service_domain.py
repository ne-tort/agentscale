"""Unit tests for project domain extensions."""

from prodavan.domain.projects import (
    PLATFORM_EVENT_TYPES,
    ProjectStatus,
    ProjectVisibilityMode,
    new_runtime_unit_id,
)


def test_completed_status_exists():
    assert ProjectStatus.COMPLETED == "completed"


def test_visibility_modes():
    assert ProjectVisibilityMode.CABINET_SHARED == "cabinet_shared"
    assert ProjectVisibilityMode.RESTRICTED == "restricted"


def test_runtime_unit_id_prefix():
    assert new_runtime_unit_id().startswith("pru_")


def test_platform_event_types_include_lifecycle_extensions():
    for event in (
        "project.started",
        "project.completed",
        "project.runtime_unit.attached",
        "project.runtime_unit.detached",
        "project.visibility.changed",
        "employee.enabled",
    ):
        assert event in PLATFORM_EVENT_TYPES
