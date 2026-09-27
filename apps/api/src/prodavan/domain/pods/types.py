"""Pod runtime domain types (module 14)."""

from __future__ import annotations

import re
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

_DNS_LABEL_MAX = 58  # pod-{label} must fit k8s 63-char name limit


def new_pod_id() -> str:
    return f"pod_{uuid.uuid4().hex[:16]}"


def sanitize_dns(value: str) -> str:
    """RFC 1123 subdomain fragment for Pod name suffix."""
    s = value.lower().replace("_", "-")
    s = re.sub(r"[^a-z0-9-]", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        s = "ws"
    if len(s) > _DNS_LABEL_MAX:
        s = s[:_DNS_LABEL_MAX].rstrip("-")
    return s


def runtime_ref_for(workspace_key: str, *, mode: str = "stub") -> str:
    if mode == "k8s":
        return f"pod-{sanitize_dns(workspace_key)}"
    if mode == "sandbox":
        # The runtime_ref *is* the SandboxClaim name (deterministic get-or-create).
        # K8s rejects ``object-ws:``-style refs used as claim names with 422.
        return f"sandbox-claim-{sanitize_dns(workspace_key)}"
    return f"object-ws:{workspace_key}"
