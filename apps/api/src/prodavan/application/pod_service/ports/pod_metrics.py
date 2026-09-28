"""PodMetricsPort — read-only resource metrics (module 14)."""

from __future__ import annotations

from typing import Any, Protocol


class PodMetricsPort(Protocol):
    async def get_pod_metrics(
        self, *, runtime_ref: str, sandbox_name: str | None = None
    ) -> dict[str, Any] | None:
        """Pod CPU/RAM sample; None when unavailable.

        ``sandbox_name``: sandbox mode only — a pre-resolved Sandbox name
        from a fresh claim status, passed through to skip a duplicate claim
        GET. k8s mode ignores it (runtime_ref IS the pod name there).
        """
        ...
