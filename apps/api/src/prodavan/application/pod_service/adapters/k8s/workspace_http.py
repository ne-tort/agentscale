"""HTTP workspace adapter — agent-runtime /v1/workspace/* (no kubectl exec).

The HTTP client logic lives in ``HttpWorkspaceAdapterBase`` and is shared with
the sandbox-mode variant (adapters/agent_sandbox/workspace_http.py): the base
addresses the runtime through a ``RuntimeEndpoint`` (base_url + extra headers),
while each subclass decides how to resolve that endpoint — direct pod IP here,
the agent-sandbox sandbox-router (X-Sandbox-* headers) in sandbox mode.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import httpx

from prodavan.application.agent.runtime_transport import RuntimeEndpoint
from prodavan.application.pod_service.ports.workspace import WorkspaceEntry
from prodavan.application.pod_service.workspace_paths import normalize_workspace_path
from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.infrastructure.k8s.sandbox.client import K8sSandboxClient

logger = logging.getLogger(__name__)

_DEFAULT_READ_MAX = 10_485_760


def _runtime_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    token = settings.pod_agent_runtime_token.strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _endpoint_headers(endpoint: RuntimeEndpoint) -> dict[str, str]:
    headers = _runtime_headers()
    headers.update(endpoint.headers)
    return headers


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


class HttpWorkspaceAdapterBase:
    """HTTP /v1/workspace client; subclasses resolve the RuntimeEndpoint."""

    async def _runtime_endpoint(self, runtime_ref: str) -> RuntimeEndpoint:
        raise NotImplementedError

    async def _write_fallback(self, *, runtime_ref: str, path: str, data: bytes) -> None:
        """Called when the runtime has no HTTP write API (404/405 on PUT)."""
        raise AppError(
            code="RUNTIME_FS_FAILED",
            title="Bad Gateway",
            status=502,
            detail="agent runtime has no workspace write API",
        )

    async def _request_json(
        self,
        *,
        runtime_ref: str,
        method: str,
        path: str,
        json_body: dict | None = None,
    ) -> dict[str, Any]:
        endpoint = await self._runtime_endpoint(runtime_ref)
        url = f"{endpoint.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(
                    method,
                    url,
                    json=json_body,
                    headers=_endpoint_headers(endpoint),
                )
        except Exception as exc:
            raise AppError(
                code="RUNTIME_UNREACHABLE",
                title="Bad Gateway",
                status=502,
                detail=str(exc),
            ) from exc
        if response.status_code >= 400:
            raise AppError(
                code="RUNTIME_FS_FAILED",
                title="Bad Gateway",
                status=502,
                detail=response.text[:500],
            )
        if response.status_code == 204:
            return {}
        return response.json()

    async def list_entries(self, *, runtime_ref: str, path: str) -> list[WorkspaceEntry]:
        rel = normalize_workspace_path(path)
        body = await self._request_json(
            runtime_ref=runtime_ref,
            method="GET",
            path=f"/v1/workspace/entries?path={quote(rel, safe='')}",
        )
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
        endpoint = await self._runtime_endpoint(runtime_ref)
        url = f"{endpoint.base_url}/v1/workspace/content?path={quote(rel, safe='')}&max_bytes={limit}"
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(url, headers=_endpoint_headers(endpoint))
        except Exception as exc:
            raise AppError(
                code="RUNTIME_UNREACHABLE",
                title="Bad Gateway",
                status=502,
                detail=str(exc),
            ) from exc
        if response.status_code >= 400:
            raise AppError(
                code="RUNTIME_FS_FAILED",
                title="Bad Gateway",
                status=502,
                detail=response.text[:500],
            )
        return response.content

    async def stat(self, *, runtime_ref: str, path: str) -> WorkspaceEntry:
        rel = normalize_workspace_path(path)
        if not rel:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="path required",
            )
        body = await self._request_json(
            runtime_ref=runtime_ref,
            method="GET",
            path=f"/v1/workspace/stat?path={quote(rel, safe='')}",
        )
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
        await self._request_json(
            runtime_ref=runtime_ref,
            method="DELETE",
            path=f"/v1/workspace/entries?path={quote(rel, safe='')}",
        )

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
        await self._request_json(
            runtime_ref=runtime_ref,
            method="POST",
            path="/v1/workspace/move",
            json_body={"src": s, "dst": d},
        )

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
        await self._request_json(
            runtime_ref=runtime_ref,
            method="POST",
            path="/v1/workspace/copy",
            json_body={"src": s, "dst": d},
        )

    async def write_bytes(self, *, runtime_ref: str, path: str, data: bytes) -> None:
        """Prefer HTTP PUT; fall back to mode-specific write when runtime has no write API."""
        rel = normalize_workspace_path(path)
        if not rel:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="path required",
            )
        endpoint = await self._runtime_endpoint(runtime_ref)
        url = f"{endpoint.base_url}/v1/workspace/content?path={quote(rel, safe='')}"
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.put(url, content=data, headers=_endpoint_headers(endpoint))
        except Exception as exc:
            raise AppError(
                code="RUNTIME_UNREACHABLE",
                title="Bad Gateway",
                status=502,
                detail=str(exc),
            ) from exc
        if response.status_code in {404, 405}:
            await self._write_fallback(runtime_ref=runtime_ref, path=rel, data=data)
            return
        if response.status_code >= 400:
            raise AppError(
                code="RUNTIME_FS_FAILED",
                title="Bad Gateway",
                status=502,
                detail=response.text[:500],
            )


class HttpAgentRuntimeWorkspaceAdapter(HttpWorkspaceAdapterBase):
    """k8s mode: direct pod-IP endpoint (legacy path, no router)."""

    def __init__(self, *, client: K8sSandboxClient) -> None:
        self._client = client

    async def _runtime_endpoint(self, runtime_ref: str) -> RuntimeEndpoint:
        snap = await self._client.get_pod(runtime_ref)
        if snap is None or not snap.ready or snap.phase != "Running" or not snap.pod_ip:
            raise AppError(
                code="POD_NOT_RUNNING",
                title="Conflict",
                status=409,
                detail="pod is not running",
            )
        return RuntimeEndpoint(
            base_url=f"http://{snap.pod_ip}:{settings.pod_agent_runtime_port}",
            headers={},
        )

    async def _write_fallback(self, *, runtime_ref: str, path: str, data: bytes) -> None:
        from prodavan.application.pod_service.adapters.k8s.workspace_exec import (
            K8sExecWorkspaceAdapter,
        )

        await K8sExecWorkspaceAdapter(client=self._client).write_bytes(
            runtime_ref=runtime_ref,
            path=path,
            data=data,
        )
