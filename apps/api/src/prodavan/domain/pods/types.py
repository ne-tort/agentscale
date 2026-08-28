"""Pod runtime domain types (module 14)."""

from __future__ import annotations

import uuid
from enum import StrEnum


class PodStatus(StrEnum):
    PENDING = "pending"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    FAILED = "failed"
    TERMINATING = "terminating"
    TERMINATED = "terminated"


class PodDesiredState(StrEnum):
    ABSENT = "absent"
    RUNNING = "running"


POD_TERMINAL_STATUSES = frozenset({PodStatus.TERMINATED, PodStatus.FAILED})


def new_pod_id() -> str:
    return f"pod_{uuid.uuid4().hex[:16]}"


def runtime_ref_for(workspace_key: str) -> str:
    return f"object-ws:{workspace_key}"
