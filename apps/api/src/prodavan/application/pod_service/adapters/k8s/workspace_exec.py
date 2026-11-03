"""K8s exec adapter for live Pod workspace filesystem."""

from __future__ import annotations

import json
import logging
from typing import Any

from prodavan.application.pod_service.adapters.k8s.exec_errors import app_error_from_exec_failure
from prodavan.application.pod_service.adapters.k8s.workspace_exec_cmd import build_workspace_fs_command
from prodavan.application.pod_service.ports.workspace import PodWorkspacePort, WorkspaceEntry
from prodavan.application.pod_service.workspace_paths import normalize_workspace_path
from prodavan.domain.errors import AppError
from prodavan.infrastructure.k8s.sandbox.client import K8sSandboxClient
from prodavan.infrastructure.k8s.sandbox.exec import exec_in_pod

logger = logging.getLogger(__name__)

_DEFAULT_READ_MAX = 10_485_760


def _normalize_response_path(raw: object) -> str:
    value = str(raw or "").replace("\\", "/")
    if value == "/workspace":
        value = ""
    elif value.startswith("/workspace/"):
        value = value[len("/workspace/") :]
    return normalize_workspace_path(value)


def _entry_from_dict(raw: dict[str, Any]) -> WorkspaceEntry:
    kind = raw.get("kind")
    if kind not in ("file", "dir"):
        kind = "file"
    return WorkspaceEntry(
        name=str(raw.get("name") or ""),
        path=_normalize_response_path(raw.get("path")),
        kind=kind,  # type: ignore[arg-type]
        size=raw.get("size") if kind == "file" else None,
        modified_at=raw.get("modified_at"),
    )


class K8sExecWorkspaceAdapter:
    def __init__(self, *, client: K8sSandboxClient) -> None:
        self._client = client

    async def _require_running_snap(self, runtime_ref: str):
        try:
            snap = await self._client.get_pod(runtime_ref)
        except AppError:
            raise
        except Exception as exc:
            raise app_error_from_exec_failure(exc) from exc
        if snap is None or not snap.ready or snap.phase != "Running":
            raise AppError(
                code="POD_NOT_RUNNING",
                title="Conflict",
                status=409,
                detail="pod is not running",
            )
        return snap

    async def _run_json(self, *, runtime_ref: str, args: list[str]) -> dict[str, Any]:
        await self._require_running_snap(runtime_ref)
        try:
            result = await exec_in_pod(
                auth=self._client.auth,
                namespace=self._client.namespace,
                pod_name=runtime_ref,
                command=build_workspace_fs_command(args),
            )
        except AppError:
            raise
        except Exception as exc:
            raise app_error_from_exec_failure(exc) from exc
        if result.exit_code not in (0, None):
            err = result.stderr.decode("utf-8", errors="replace")[:500]
            raise AppError(
                code="POD_EXEC_FAILED",
                title="Bad Gateway",
                status=502,
                detail=err or "pod exec failed",
            )
        try:
            return json.loads(result.stdout.decode("utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning("workspace_fs invalid json stdout=%r", result.stdout[:200])
            raise AppError(
                code="POD_EXEC_FAILED",
                title="Bad Gateway",
                status=502,
                detail="invalid workspace_fs output",
            ) from exc

    async def _run_bytes(self, *, runtime_ref: str, args: list[str]) -> bytes:
        await self._require_running_snap(runtime_ref)
        try:
            result = await exec_in_pod(
                auth=self._client.auth,
                namespace=self._client.namespace,
                pod_name=runtime_ref,
                command=build_workspace_fs_command(args),
            )
        except AppError:
            raise
        except Exception as exc:
            raise app_error_from_exec_failure(exc) from exc
        if result.exit_code not in (0, None):
            err = result.stderr.decode("utf-8", errors="replace")[:500]
            raise AppError(
                code="POD_EXEC_FAILED",
                title="Bad Gateway",
                status=502,
                detail=err or "pod exec failed",
            )
        return result.stdout

    async def list_entries(self, *, runtime_ref: str, path: str) -> list[WorkspaceEntry]:
        rel = normalize_workspace_path(path)
        body = await self._run_json(runtime_ref=runtime_ref, args=["list", rel])
        items = body.get("entries") or []
        out: list[WorkspaceEntry] = []
        for item in items:
            if isinstance(item, dict):
                out.append(_entry_from_dict(item))
        return out

    async def create_directory(self, *, runtime_ref: str, path: str) -> None:
        rel = normalize_workspace_path(path)
        if not rel:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="path required",
            )
        try:
            body = await self._run_json(runtime_ref=runtime_ref, args=["mkdir", rel])
        except AppError as exc:
            detail = exc.detail or ""
            if exc.code == "POD_EXEC_FAILED":
                if "FileExistsError" in detail:
                    raise AppError(
                        code="WORKSPACE_ALREADY_EXISTS",
                        title="Conflict",
                        status=409,
                        detail="workspace path already exists",
                        extra={"path": rel},
                    ) from exc
                if "NotADirectoryError" in detail:
                    raise AppError(
                        code="WORKSPACE_PATH_CONFLICT",
                        title="Conflict",
                        status=409,
                        detail="a parent workspace path is not a directory",
                        extra={"path": rel},
                    ) from exc
                if "PermissionError" in detail:
                    raise AppError(
                        code="WORKSPACE_PERMISSION_DENIED",
                        title="Forbidden",
                        status=403,
                        detail="permission denied creating workspace directory",
                        extra={"path": rel},
                    ) from exc
                if "FileNotFoundError" in detail:
                    raise AppError(
                        code="WORKSPACE_NOT_FOUND",
                        title="Not Found",
                        status=404,
                        detail="workspace parent path not found",
                        extra={"path": rel},
                    ) from exc
            raise
        if body.get("created") is not True or _normalize_response_path(body.get("path")) != rel:
            raise AppError(
                code="POD_EXEC_FAILED",
                title="Bad Gateway",
                status=502,
                detail="workspace mkdir result did not match requested path",
            )

    async def read_bytes(self, *, runtime_ref: str, path: str, max_bytes: int) -> bytes:
        rel = normalize_workspace_path(path)
        if not rel:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="path required",
            )
        limit = max(1, min(int(max_bytes), _DEFAULT_READ_MAX))
        return await self._run_bytes(
            runtime_ref=runtime_ref,
            args=["read", rel, "--max-bytes", str(limit)],
        )

    async def stat(self, *, runtime_ref: str, path: str) -> WorkspaceEntry:
        rel = normalize_workspace_path(path)
        if not rel:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="path required",
            )
        try:
            body = await self._run_json(runtime_ref=runtime_ref, args=["stat", rel])
        except AppError as exc:
            if exc.code == "POD_EXEC_FAILED" and "FileNotFoundError" in (exc.detail or ""):
                raise AppError(
                    code="WORKSPACE_NOT_FOUND",
                    title="Not Found",
                    status=404,
                    detail="workspace path not found",
                    extra={"path": rel},
                ) from exc
            raise
        return _entry_from_dict(body)

    async def delete(self, *, runtime_ref: str, path: str) -> None:
        rel = normalize_workspace_path(path)
        if not rel:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="path required",
            )
        try:
            await self._run_bytes(runtime_ref=runtime_ref, args=["rm", rel])
        except AppError as exc:
            if exc.code == "POD_EXEC_FAILED" and "FileNotFoundError" in (exc.detail or ""):
                raise AppError(
                    code="WORKSPACE_NOT_FOUND",
                    title="Not Found",
                    status=404,
                    detail="workspace path not found",
                    extra={"path": rel},
                ) from exc
            raise

    async def move(self, *, runtime_ref: str, src: str, dst: str) -> None:
        s = normalize_workspace_path(src)
        d = normalize_workspace_path(dst)
        if not s or not d:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="src and dst required",
            )
        await self._run_bytes(runtime_ref=runtime_ref, args=["mv", s, d])

    async def copy(self, *, runtime_ref: str, src: str, dst: str) -> None:
        s = normalize_workspace_path(src)
        d = normalize_workspace_path(dst)
        if not s or not d:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="src and dst required",
            )
        await self._run_bytes(runtime_ref=runtime_ref, args=["cp", s, d])

    async def write_bytes(self, *, runtime_ref: str, path: str, data: bytes) -> None:
        rel = normalize_workspace_path(path)
        if not rel:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="path required",
            )
        await self._require_running_snap(runtime_ref)
        try:
            result = await exec_in_pod(
                auth=self._client.auth,
                namespace=self._client.namespace,
                pod_name=runtime_ref,
                command=build_workspace_fs_command(["write", rel]),
                stdin_data=data,
                timeout=120.0,
            )
        except AppError:
            raise
        except Exception as exc:
            raise app_error_from_exec_failure(exc) from exc
        if result.exit_code not in (0, None):
            err = result.stderr.decode("utf-8", errors="replace")[:500]
            raise AppError(
                code="POD_EXEC_FAILED",
                title="Bad Gateway",
                status=502,
                detail=err or "pod exec failed",
            )


def as_port(adapter: K8sExecWorkspaceAdapter) -> PodWorkspacePort:
    return adapter
