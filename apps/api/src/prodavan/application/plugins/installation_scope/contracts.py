"""Shared installation scope evidence, never a client-provided scope."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InstallationScope:
    """Exact durable installation parent, validated against ProjectRow."""

    company_id: str
    plugin_instance_id: str
    cabinet_id: str | None = None
    project_id: str | None = None


__all__ = ["InstallationScope"]
