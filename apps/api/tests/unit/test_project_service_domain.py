"""Unit tests for project domain extensions."""

from prodavan.domain.pods import new_pod_id
from prodavan.domain.projects import (
    PLATFORM_EVENT_TYPES,
    ProjectStatus,
    ProjectVisibilityMode,
)


def test_completed_status_exists():
    assert ProjectStatus.COMPLETED == "completed"


def test_visibility_modes():
    assert ProjectVisibilityMode.CABINET_SHARED == "cabinet_shared"
    assert ProjectVisibilityMode.RESTRICTED == "restricted"


def test_pod_id_prefix():
    assert new_pod_id().startswith("pod_")


def test_platform_event_types_include_lifecycle_extensions():
    for event in (
        "project.started",
        "project.completed",
        "project.visibility.changed",
        "pod.started",
        "pod.paused",
        "employee.enabled",
    ):
        assert event in PLATFORM_EVENT_TYPES
