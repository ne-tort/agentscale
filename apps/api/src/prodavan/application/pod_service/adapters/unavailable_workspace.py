"""Workspace adapter when live pod filesystem is unavailable (stub mode)."""

from __future__ import annotations

from prodavan.application.pod_service.ports.workspace import WorkspaceEntry
from prodavan.domain.errors import AppError


class UnavailableWorkspaceAdapter:
    async def list_entries(self, *, runtime_ref: str, path: str) -> list[WorkspaceEntry]:
        _ = runtime_ref, path
        raise AppError(
            code="POD_NOT_RUNNING",
            title="Conflict",
            status=409,
            detail="pod is not running",
        )

    async def read_bytes(self, *, runtime_ref: str, path: str, max_bytes: int) -> bytes:
        _ = runtime_ref, path, max_bytes
        raise AppError(
            code="POD_NOT_RUNNING",
            title="Conflict",
            status=409,
            detail="pod is not running",
        )

    async def stat(self, *, runtime_ref: str, path: str) -> WorkspaceEntry:
        _ = runtime_ref, path
        raise AppError(
            code="POD_NOT_RUNNING",
            title="Conflict",
            status=409,
            detail="pod is not running",
        )

    async def delete(self, *, runtime_ref: str, path: str) -> None:
        _ = runtime_ref, path
        raise AppError(
            code="POD_NOT_RUNNING",
            title="Conflict",
            status=409,
            detail="pod is not running",
        )

    async def move(self, *, runtime_ref: str, src: str, dst: str) -> None:
        _ = runtime_ref, src, dst
        raise AppError(
            code="POD_NOT_RUNNING",
            title="Conflict",
            status=409,
            detail="pod is not running",
        )

    async def copy(self, *, runtime_ref: str, src: str, dst: str) -> None:
        _ = runtime_ref, src, dst
        raise AppError(
            code="POD_NOT_RUNNING",
            title="Conflict",
            status=409,
            detail="pod is not running",
        )

    async def write_bytes(self, *, runtime_ref: str, path: str, data: bytes) -> None:
        _ = runtime_ref, path, data
        raise AppError(
            code="POD_NOT_RUNNING",
            title="Conflict",
            status=409,
            detail="pod is not running",
        )
