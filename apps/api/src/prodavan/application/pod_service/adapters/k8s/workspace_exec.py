"""K8s exec adapter for live Pod workspace filesystem."""

from __future__ import annotations

import json
import logging
from typing import Any

from prodavan.application.pod_service.adapters.k8s.workspace_exec_cmd import build_workspace_fs_command
from prodavan.application.pod_service.ports.workspace import PodWorkspacePort, WorkspaceEntry
from prodavan.application.pod_service.workspace_paths import normalize_workspace_path
from prodavan.domain.errors import AppError
from prodavan.infrastructure.k8s.sandbox.client import K8sSandboxClient
from prodavan.infrastructure.k8s.sandbox.exec import exec_in_pod

logger = logging.getLogger(__name__)

_DEFAULT_READ_MAX = 10_485_760


def _entry_from_dict(raw: dict[str, Any]) -> WorkspaceEntry:
    kind = raw.get("kind")
    if kind not in ("file", "dir"):
        kind = "file"
    return WorkspaceEntry(
        name=str(raw.get("name") or ""),
        path=str(raw.get("path") or ""),
        kind=kind,  # type: ignore[arg-type]
        size=raw.get("size") if kind == "file" else None,
        modified_at=raw.get("modified_at"),
    )


class K8sExecWorkspaceAdapter:
    def __init__(self, *, client: K8sSandboxClient) -> None:
        self._client = client

    async def _run_json(self, *, runtime_ref: str, args: list[str]) -> dict[str, Any]:
        snap = await self._client.get_pod(runtime_ref)
        if snap is None or not snap.ready or snap.phase != "Running":
            raise AppError(
                code="POD_NOT_RUNNING",
                title="Conflict",
                status=409,
                detail="pod is not running",
            )
        result = await exec_in_pod(
            auth=self._client.auth,
            namespace=self._client.namespace,
            pod_name=runtime_ref,
            command=build_workspace_fs_command(args),
        )
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
        snap = await self._client.get_pod(runtime_ref)
        if snap is None or not snap.ready or snap.phase != "Running":
            raise AppError(
                code="POD_NOT_RUNNING",
                title="Conflict",
                status=409,
                detail="pod is not running",
            )
        result = await exec_in_pod(
            auth=self._client.auth,
            namespace=self._client.namespace,
            pod_name=runtime_ref,
            command=build_workspace_fs_command(args),
        )
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
        body = await self._run_json(runtime_ref=runtime_ref, args=["stat", rel])
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
        await self._run_bytes(runtime_ref=runtime_ref, args=["rm", rel])

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


def as_port(adapter: K8sExecWorkspaceAdapter) -> PodWorkspacePort:
    return adapter
