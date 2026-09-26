"""Derived pod/container state — live observation, not DB intent."""

from __future__ import annotations

from enum import StrEnum


class ObservedState(StrEnum):
    ABSENT = "absent"
    PREPARING = "preparing"
    PROVISIONING = "provisioning"
    PULLING = "pulling"
    HYDRATING = "hydrating"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    PAUSED = "paused"
    SUSPENDED = "suspended"
    PAUSING = "pausing"
