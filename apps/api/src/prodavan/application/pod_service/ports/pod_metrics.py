"""PodMetricsPort — read-only resource metrics (module 14)."""

from __future__ import annotations

from typing import Any, Protocol


class PodMetricsPort(Protocol):
    async def get_pod_metrics(self, *, runtime_ref: str) -> dict[str, Any] | None: ...
