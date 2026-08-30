"""Pod workspace filesystem — live Pod /workspace access."""

from __future__ import annotations

import mimetypes
from dataclasses import asdict

from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.pod_service.factory import build_pod_workspace
from prodavan.application.pod_service.ports.workspace import PodWorkspacePort, WorkspaceEntry
from prodavan.application.pod_service.query import PodQuery
from prodavan.application.pod_service.workspace_paths import normalize_workspace_path
from prodavan.application.project_service.access import ProjectAccessPolicy
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

_PREVIEW_MAX_BYTES = 262_144
_DOWNLOAD_MAX_BYTES = 10_485_760
_TEXT_PREFIXES = (
    "text/",
    "application/json",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
)


def _entry_dict(entry: WorkspaceEntry) -> dict:
    return asdict(entry)


def _is_text_content(data: bytes, filename: str) -> bool:
    if not data:
        return True
    if b"\x00" in data[:8192]:
        return False
    guessed, _ = mimetypes.guess_type(filename)
    if guessed and (guessed.startswith("text/") or guessed in _TEXT_PREFIXES):
        return True
    if filename.endswith((".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv", ".log", ".py", ".sh")):
        return True
    try:
        data[:8192].decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


class PodWorkspaceService:
    def __init__(self, session: AsyncSession, *, workspace: PodWorkspacePort | None = None) -> None:
        self._session = session
        self._access = ProjectAccessPolicy(session)
        self._pods = PodQuery(session)
        self._workspace = workspace or build_pod_workspace()

    async def _require_running_runtime(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        write: bool = False,
    ) -> tuple[str, str]:
        await self._access.require_access(
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=write,
            allow_paused=True,
        )
        runtime = await self._pods.runtime_view(project_id)
        if runtime is None:
            raise AppError(
                code="POD_NOT_RUNNING",
                title="Conflict",
                status=409,
                detail="pod is not running",
            )
        if runtime.get("stub"):
            raise AppError(
                code="POD_NOT_RUNNING",
                title="Conflict",
                status=409,
                detail="pod is not running",
            )
        if runtime.get("observed_state") != "running":
            raise AppError(
                code="POD_NOT_RUNNING",
                title="Conflict",
                status=409,
                detail="pod is not running",
            )
        runtime_ref = str(runtime.get("k8s_pod_name") or runtime.get("runtime_ref") or "").strip()
        if not runtime_ref:
            raise AppError(
                code="POD_NOT_RUNNING",
                title="Conflict",
                status=409,
                detail="pod is not running",
            )
        return runtime_ref, normalize_workspace_path("")

    async def list_entries(
        self,
        *,
        project_id: str,
        path: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        runtime_ref, _ = await self._require_running_runtime(
            project_id=project_id, principal=principal, employee=employee
        )
        rel = normalize_workspace_path(path)
        entries = await self._workspace.list_entries(runtime_ref=runtime_ref, path=rel)
        return {"path": rel, "entries": [_entry_dict(e) for e in entries]}

    async def read_content(
        self,
        *,
        project_id: str,
        path: str,
        principal: Principal,
        employee: EmployeeRow | None,
        max_bytes: int = _DOWNLOAD_MAX_BYTES,
    ) -> tuple[bytes, WorkspaceEntry]:
        runtime_ref, _ = await self._require_running_runtime(
            project_id=project_id, principal=principal, employee=employee
        )
        rel = normalize_workspace_path(path)
        if not rel:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="path required",
            )
        entry = await self._workspace.stat(runtime_ref=runtime_ref, path=rel)
        if entry.kind != "file":
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="not a file",
            )
        data = await self._workspace.read_bytes(
            runtime_ref=runtime_ref,
            path=rel,
            max_bytes=max_bytes,
        )
        return data, entry

    async def preview_text(
        self,
        *,
        project_id: str,
        path: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        data, entry = await self.read_content(
            project_id=project_id,
            path=path,
            principal=principal,
            employee=employee,
            max_bytes=_PREVIEW_MAX_BYTES + 1,
        )
        truncated = len(data) > _PREVIEW_MAX_BYTES
        if truncated:
            data = data[:_PREVIEW_MAX_BYTES]
        if not _is_text_content(data, entry.name):
            raise AppError(
                code="UNSUPPORTED_MEDIA",
                title="Unsupported Media Type",
                status=415,
                detail="not a text file",
            )
        return {
            "path": entry.path,
            "name": entry.name,
            "size": entry.size,
            "truncated": truncated,
            "content": data.decode("utf-8", errors="replace"),
        }

    async def delete_entry(
        self,
        *,
        project_id: str,
        path: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        runtime_ref, _ = await self._require_running_runtime(
            project_id=project_id, principal=principal, employee=employee, write=True
        )
        rel = normalize_workspace_path(path)
        if not rel:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="path required",
            )
        await self._workspace.delete(runtime_ref=runtime_ref, path=rel)
        return {"deleted": True, "path": rel}

    async def move_entry(
        self,
        *,
        project_id: str,
        src: str,
        dst: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        runtime_ref, _ = await self._require_running_runtime(
            project_id=project_id, principal=principal, employee=employee, write=True
        )
        s = normalize_workspace_path(src)
        d = normalize_workspace_path(dst)
        await self._workspace.move(runtime_ref=runtime_ref, src=s, dst=d)
        return {"moved": True, "src": s, "dst": d}

    async def copy_entry(
        self,
        *,
        project_id: str,
        src: str,
        dst: str,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        runtime_ref, _ = await self._require_running_runtime(
            project_id=project_id, principal=principal, employee=employee, write=True
        )
        s = normalize_workspace_path(src)
        d = normalize_workspace_path(dst)
        await self._workspace.copy(runtime_ref=runtime_ref, src=s, dst=d)
        return {"copied": True, "src": s, "dst": d}
