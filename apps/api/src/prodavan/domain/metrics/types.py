"""Metrics BC domain types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

METRICS_EVENT_TYPES = frozenset(
    {
        "pod.metrics.sample",
        "pod.metrics.degraded",
    }
)

METRIC_WINDOWS = frozenset({"1h"})


@dataclass(slots=True)
class MetricSample:
    project_id: str
    pod_id: str | None
    company_id: str | None
    cabinet_id: str | None
    cpu_millicores: int | None = None
    memory_bytes: int | None = None
    phase: str | None = None
    restarts: int | None = None
    ready: bool | None = None
    timestamp: str = ""
    degraded: bool = False
    degraded_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "project_id": self.project_id,
            "pod_id": self.pod_id,
            "company_id": self.company_id,
            "cabinet_id": self.cabinet_id,
            "timestamp": self.timestamp,
        }
        if self.cpu_millicores is not None:
            out["cpu_millicores"] = self.cpu_millicores
        if self.memory_bytes is not None:
            out["memory_bytes"] = self.memory_bytes
        if self.phase is not None:
            out["phase"] = self.phase
        if self.restarts is not None:
            out["restarts"] = self.restarts
        if self.ready is not None:
            out["ready"] = self.ready
        if self.degraded:
            out["degraded"] = True
            if self.degraded_reason:
                out["degraded_reason"] = self.degraded_reason
        if self.extra:
            out.update(self.extra)
        return out

    @classmethod
    def from_payload(
        cls,
        *,
        project_id: str,
        company_id: str | None,
        cabinet_id: str | None,
        payload: dict[str, Any],
        timestamp: str,
    ) -> MetricSample:
        return cls(
            project_id=project_id,
            pod_id=payload.get("pod_id"),
            company_id=company_id,
            cabinet_id=cabinet_id,
            cpu_millicores=_optional_int(payload.get("cpu_millicores")),
            memory_bytes=_optional_int(payload.get("memory_bytes")),
            phase=_optional_str(payload.get("phase")),
            restarts=_optional_int(payload.get("restarts")),
            ready=payload.get("ready") if isinstance(payload.get("ready"), bool) else None,
            timestamp=timestamp,
            degraded=bool(payload.get("degraded")),
            degraded_reason=_optional_str(payload.get("degraded_reason")),
        )


@dataclass(frozen=True, slots=True)
class MetricWindow:
    name: str = "1h"
    max_points: int = 60


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
