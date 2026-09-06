"""DehydratePort — live Pod /workspace → MinIO last-good tree."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class DehydrateResult:
    uploaded: int
    deleted: int
    skipped: int = 0


class DehydratePort(Protocol):
    async def dehydrate(
        self,
        *,
        workspace_key: str,
        runtime_ref: str,
    ) -> DehydrateResult: ...
