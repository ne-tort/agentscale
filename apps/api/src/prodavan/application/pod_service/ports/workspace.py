"""PodWorkspacePort — live filesystem access inside running sandbox Pod."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class WorkspaceEntry:
    name: str
    path: str
    kind: Literal["file", "dir"]
    size: int | None
    modified_at: str | None


class PodWorkspacePort(Protocol):
    async def list_entries(self, *, runtime_ref: str, path: str) -> list[WorkspaceEntry]: ...

    async def read_bytes(self, *, runtime_ref: str, path: str, max_bytes: int) -> bytes: ...

    async def stat(self, *, runtime_ref: str, path: str) -> WorkspaceEntry: ...

    async def delete(self, *, runtime_ref: str, path: str) -> None: ...

    async def move(self, *, runtime_ref: str, src: str, dst: str) -> None: ...

    async def copy(self, *, runtime_ref: str, src: str, dst: str) -> None: ...

    async def write_bytes(self, *, runtime_ref: str, path: str, data: bytes) -> None: ...
