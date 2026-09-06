"""K8s dehydrate — tar /workspace from running Pod, upload last-good to object store."""

from __future__ import annotations

import logging

from prodavan.application.pod_service.adapters.k8s.exec_errors import app_error_from_exec_failure
from prodavan.application.pod_service.ports.dehydrate import DehydrateResult
from prodavan.application.pod_service.workspace_dehydrate_rules import tar_exclude_args
from prodavan.application.pod_service.workspace_tar_upload import upload_workspace_tar
from prodavan.domain.errors import AppError
from prodavan.infrastructure.k8s.sandbox.client import K8sSandboxClient
from prodavan.infrastructure.k8s.sandbox.exec import exec_in_pod

logger = logging.getLogger(__name__)

_DEHYDRATE_TIMEOUT_S = 120.0


def _tar_command() -> list[str]:
    return [
        "tar",
        "czf",
        "-",
        "-C",
        "/workspace",
        *tar_exclude_args(),
        ".",
    ]


class K8sDehydrateAdapter:
    def __init__(self, *, client: K8sSandboxClient) -> None:
        self._client = client

    async def dehydrate(self, *, workspace_key: str, runtime_ref: str) -> DehydrateResult:
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
                detail="pod is not running; cannot dehydrate workspace",
            )
        try:
            result = await exec_in_pod(
                auth=self._client.auth,
                namespace=self._client.namespace,
                pod_name=runtime_ref,
                command=_tar_command(),
                timeout=_DEHYDRATE_TIMEOUT_S,
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
                detail=err or "dehydrate tar failed",
            )
        if not result.stdout:
            logger.warning(
                "dehydrate empty tar; skip object-store mutate workspace_key=%s runtime_ref=%s",
                workspace_key,
                runtime_ref,
            )
            return DehydrateResult(uploaded=0, deleted=0)
        return upload_workspace_tar(workspace_key=workspace_key, tar_bytes=result.stdout)
