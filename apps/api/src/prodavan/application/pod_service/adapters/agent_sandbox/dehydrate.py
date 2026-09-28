"""Sandbox dehydrate — stream workspace archive via sandbox-router → object store.

The agent-runtime exposes ``GET /v1/workspace/archive`` (tar.gz stream of the
workspace PVC root). In sandbox mode the claim's backing Sandbox (pod) is
addressed through the agent-sandbox sandbox-router (``X-Sandbox-*`` headers) —
never by pod IP — because the Sandbox is re-adoptable and its name differs
from the SandboxClaim name used as ``runtime_ref``.

B5: a router 404/410 triggers ONE re-resolve + retry — the sandbox may have
been re-adopted under a new name (the claim→sandbox cache is invalidated
first). B9: the archive is spooled to a temp file (64MiB in RAM, then disk)
and the MinIO upload runs in a worker thread, so large workspaces neither
balloon memory nor block the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Any, BinaryIO

import httpx

from prodavan.application.agent.runtime_auth import runtime_auth_headers
from prodavan.application.agent.runtime_transport import (
    RuntimeEndpoint,
    invalidate_sandbox_name_cache,
    resolve_runtime_endpoint_for_ref,
)
from prodavan.application.pod_service.ports.dehydrate import DehydrateResult
from prodavan.application.pod_service.workspace_tar_upload import upload_workspace_tar
from prodavan.domain.errors import AppError

logger = logging.getLogger(__name__)

_DEHYDRATE_TIMEOUT_S = 120.0
# Keep small archives in RAM; larger ones roll over to a temp file on disk.
_ARCHIVE_SPOOL_MAX_BYTES = 64 * 1024 * 1024


class _StaleSandboxRouteError(RuntimeError):
    """Router 404/410 — the sandbox was re-adopted; re-resolve and retry."""


def _runtime_headers(extra: dict[str, str]) -> dict[str, str]:
    headers: dict[str, str] = dict(runtime_auth_headers())
    headers.update(extra)
    return headers


class SandboxHttpDehydrateAdapter:
    """DehydratePort for pod_runtime_mode=sandbox (HTTP archive via router)."""

    def __init__(
        self,
        *,
        runtime: Any | None = None,
        http_client: type[httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        self._runtime = runtime
        self._http_client = http_client

    async def _resolve_endpoint(self, runtime_ref: str) -> RuntimeEndpoint:
        endpoint = await resolve_runtime_endpoint_for_ref(runtime_ref, runtime=self._runtime)
        if endpoint is None:
            raise AppError(
                code="POD_NOT_RUNNING",
                title="Conflict",
                status=409,
                detail="pod is not running; cannot dehydrate workspace",
            )
        return endpoint

    async def _download_archive(self, endpoint: RuntimeEndpoint) -> BinaryIO:
        """Stream the workspace archive into a spooled temp file (seeked to 0)."""
        url = f"{endpoint.base_url}/v1/workspace/archive"
        spool = tempfile.SpooledTemporaryFile(max_size=_ARCHIVE_SPOOL_MAX_BYTES, mode="w+b")
        try:
            async with self._http_client(timeout=_DEHYDRATE_TIMEOUT_S) as client:
                async with client.stream(
                    "GET",
                    url,
                    headers=_runtime_headers(endpoint.headers),
                ) as response:
                    if response.status_code in (404, 410):
                        raise _StaleSandboxRouteError(
                            f"router answered {response.status_code} for workspace archive"
                        )
                    if response.status_code >= 400:
                        detail = (await response.aread()).decode("utf-8", errors="replace")[:500]
                        raise AppError(
                            code="DEHYDRATE_FAILED",
                            title="Bad Gateway",
                            status=502,
                            detail=detail or "workspace archive failed",
                        )
                    async for chunk in response.aiter_bytes():
                        spool.write(chunk)
        except (AppError, _StaleSandboxRouteError):
            spool.close()
            raise
        except Exception as exc:
            spool.close()
            raise AppError(
                code="RUNTIME_UNREACHABLE",
                title="Bad Gateway",
                status=502,
                detail=str(exc),
            ) from exc
        spool.seek(0)
        return spool

    async def dehydrate(self, *, workspace_key: str, runtime_ref: str) -> DehydrateResult:
        endpoint = await self._resolve_endpoint(runtime_ref)
        spool: BinaryIO | None = None
        for attempt in (0, 1):
            try:
                spool = await self._download_archive(endpoint)
                break
            except _StaleSandboxRouteError:
                if attempt:
                    raise AppError(
                        code="DEHYDRATE_FAILED",
                        title="Bad Gateway",
                        status=502,
                        detail="workspace archive failed after endpoint re-resolve",
                    ) from None
                # Sandbox re-adopted under a new name: drop the cached
                # claim→sandbox mapping and retry once against the fresh one.
                await invalidate_sandbox_name_cache(
                    runtime_ref=runtime_ref,
                    sandbox_name=str(endpoint.headers.get("X-Sandbox-Id") or "") or None,
                )
                endpoint = await self._resolve_endpoint(runtime_ref)
        assert spool is not None  # the loop either breaks with a spool or raises
        try:
            spool.seek(0, os.SEEK_END)
            size = spool.tell()
            spool.seek(0)
            if size == 0:
                logger.warning(
                    "dehydrate empty archive; skip object-store mutate workspace_key=%s runtime_ref=%s",
                    workspace_key,
                    runtime_ref,
                )
                return DehydrateResult(uploaded=0, deleted=0)
            # The MinIO PUTs inside upload_workspace_tar are synchronous — run
            # them in a worker thread so the event loop is not blocked (B9).
            return await asyncio.to_thread(
                upload_workspace_tar,
                workspace_key=workspace_key,
                tar_file=spool,
            )
        finally:
            spool.close()
