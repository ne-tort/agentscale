"""Pod workspace filesystem — live Pod /workspace access."""

from __future__ import annotations

import mimetypes
from dataclasses import asdict

from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.agent.runtime_guard import require_running_pod_runtime
from prodavan.application.pod_service.factory import build_pod_workspace
from prodavan.application.pod_service.ports.workspace import PodWorkspacePort, WorkspaceEntry
from prodavan.application.pod_service.workspace_paths import normalize_workspace_path
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.identity import EmployeeRow

_PREVIEW_MAX_BYTES = 262_144
_DOWNLOAD_MAX_BYTES = 10_485_760
_UPLOAD_MAX_BYTES = 10_485_760
_TEXT_PREFIXES = (
    "text/",
    "application/json",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
)


def _entry_dict(entry: WorkspaceEntry) -> dict:
    return asdict(entry)


def workspace_download_response(data: bytes, entry: WorkspaceEntry):
    """Build the shared download contract for every workspace API surface."""
    safe_name = entry.name.replace('"', "")
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "X-Workspace-Path": entry.path,
            "X-Workspace-Size": str(entry.size if entry.size is not None else len(data)),
        },
    )


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
        self._workspace = workspace or build_pod_workspace()

    async def _require_running_runtime(
        self,
        *,
        project_id: str,
        principal: Principal,
        employee: EmployeeRow | None,
        write: bool = False,
    ) -> tuple[str, str]:
        runtime = await require_running_pod_runtime(
            self._session,
            project_id=project_id,
            principal=principal,
            employee=employee,
            write=write,
        )
        runtime_ref = str(runtime.get("k8s_pod_name") or runtime.get("runtime_ref") or "").strip()
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
        allow_truncated: bool = False,
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
        if not allow_truncated and entry.size is not None and entry.size > max_bytes:
            raise AppError(
                code="PAYLOAD_TOO_LARGE",
                title="Payload Too Large",
                status=413,
                detail=f"file exceeds the {max_bytes} byte download limit",
                extra={"max_bytes": max_bytes, "size": entry.size},
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
            allow_truncated=True,
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

    async def write_content(
        self,
        *,
        project_id: str,
        path: str,
        data: bytes,
        principal: Principal,
        employee: EmployeeRow | None,
    ) -> dict:
        if len(data) > _UPLOAD_MAX_BYTES:
            raise AppError(
                code="PAYLOAD_TOO_LARGE",
                title="Payload Too Large",
                status=413,
                detail=f"file exceeds the {_UPLOAD_MAX_BYTES} byte upload limit",
                extra={"max_bytes": _UPLOAD_MAX_BYTES, "size": len(data)},
            )
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
        try:
            existing = await self._workspace.stat(runtime_ref=runtime_ref, path=rel)
        except KeyError:
            existing = None
        except AppError as exc:
            if exc.code != "WORKSPACE_NOT_FOUND":
                raise
            existing = None
        if existing is not None and existing.kind != "file":
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="path is a directory",
                extra={"path": rel, "kind": existing.kind},
            )
        await self._workspace.write_bytes(runtime_ref=runtime_ref, path=rel, data=data)
        return {"written": True, "path": rel, "size": len(data)}

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
        if not s or not d:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="src and dst required",
            )
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
        if not s or not d:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="src and dst required",
            )
        await self._workspace.copy(runtime_ref=runtime_ref, src=s, dst=d)
        return {"copied": True, "src": s, "dst": d}
