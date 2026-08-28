"""Pod runtime context passed to infrastructure adapters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PodRuntimeContext:
    pod_id: str
    project_id: str
    company_id: str
    workspace_key: str
    hydrate_generation: int = 0
