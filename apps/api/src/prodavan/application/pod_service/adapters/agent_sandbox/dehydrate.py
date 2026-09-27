"""Sandbox dehydrate — stream workspace archive via sandbox-router → object store.

The agent-runtime exposes ``GET /v1/workspace/archive`` (tar.gz stream of the
workspace PVC root). In sandbox mode the claim's backing Sandbox (pod) is
addressed through the agent-sandbox sandbox-router (``X-Sandbox-*`` headers) —
never by pod IP — because the Sandbox is re-adoptable and its name differs
from the SandboxClaim name used as ``runtime_ref``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from prodavan.application.agent.runtime_auth import runtime_auth_headers
from prodavan.application.agent.runtime_transport import resolve_runtime_endpoint_for_ref
from prodavan.application.pod_service.ports.dehydrate import DehydrateResult
from prodavan.application.pod_service.workspace_tar_upload import upload_workspace_tar
from prodavan.domain.errors import AppError

logger = logging.getLogger(__name__)

_DEHYDRATE_TIMEOUT_S = 120.0


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

    async def dehydrate(self, *, workspace_key: str, runtime_ref: str) -> DehydrateResult:
        endpoint = await resolve_runtime_endpoint_for_ref(runtime_ref, runtime=self._runtime)
        if endpoint is None:
            raise AppError(
                code="POD_NOT_RUNNING",
                title="Conflict",
                status=409,
                detail="pod is not running; cannot dehydrate workspace",
            )
        url = f"{endpoint.base_url}/v1/workspace/archive"
        try:
            async with self._http_client(timeout=_DEHYDRATE_TIMEOUT_S) as client:
                async with client.stream(
                    "GET",
                    url,
                    headers=_runtime_headers(endpoint.headers),
                ) as response:
                    if response.status_code >= 400:
                        detail = (await response.aread()).decode("utf-8", errors="replace")[:500]
                        raise AppError(
                            code="DEHYDRATE_FAILED",
                            title="Bad Gateway",
                            status=502,
                            detail=detail or "workspace archive failed",
                        )
                    chunks: list[bytes] = []
                    async for chunk in response.aiter_bytes():
                        chunks.append(chunk)
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                code="RUNTIME_UNREACHABLE",
                title="Bad Gateway",
                status=502,
                detail=str(exc),
            ) from exc
        tar_bytes = b"".join(chunks)
        if not tar_bytes:
            logger.warning(
                "dehydrate empty archive; skip object-store mutate workspace_key=%s runtime_ref=%s",
                workspace_key,
                runtime_ref,
            )
            return DehydrateResult(uploaded=0, deleted=0)
        return upload_workspace_tar(workspace_key=workspace_key, tar_bytes=tar_bytes)
