"""HydratePort — MinIO workspace into Pod /workspace (module 14)."""

from __future__ import annotations

from typing import Protocol


class HydratePort(Protocol):
    async def hydrate(self, *, workspace_key: str, runtime_ref: str) -> None: ...
