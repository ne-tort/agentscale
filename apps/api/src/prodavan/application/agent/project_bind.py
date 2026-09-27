"""Post-Ready project bind — push project identity + bridge JWT into agent-runtime.

Why: in sandbox mode (agent-sandbox) the Pod backing a SandboxClaim is adopted
from a warm pool and can be re-created at any time; a fresh runtime then has no
in-memory project identity (project_id, bridge auth token, container env). The
API therefore pushes ``POST /v1/project/bind`` with the current identity before
(re-)registering agent sessions. The runtime self-hydrates from this payload.

Bind is idempotent and cheap. Runtimes without the route (old images during the
transition period) answer 404 — tolerated with a warning, sessions still
register. This function never raises: it is strictly best-effort.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy import select

from prodavan.application.agent.runtime_auth import runtime_auth_headers
from prodavan.application.agent.runtime_transport import (
    RuntimeEndpoint,
    resolve_runtime_endpoint,
)
from prodavan.application.pod_identity.bridge import build_launch_scopes, mint_pod_bridge_token
from prodavan.application.pod_service.container_env_loader import ContainerEnvLoader
from prodavan.config.settings import settings
from prodavan.domain.pods import POD_TERMINAL_STATUSES
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Bind answers AFTER the runtime's self-hydrate completes (sync contract), so the
# timeout must cover a worst-case workspace restore - not the usual no-op re-bind.
_BIND_TIMEOUT_S = 150.0


def _bind_request_headers(endpoint: RuntimeEndpoint) -> dict[str, str]:
    headers: dict[str, str] = dict(runtime_auth_headers())
    headers.update(endpoint.headers)
    return headers


async def _resolve_live_pod(session: AsyncSession, project_id: str) -> ProjectPodRow | None:
    q = await session.execute(
        select(ProjectPodRow)
        .where(ProjectPodRow.project_id == project_id)
        .where(ProjectPodRow.status.notin_(tuple(POD_TERMINAL_STATUSES)))
        .order_by(ProjectPodRow.created_at.desc())
        .limit(1)
    )
    return q.scalar_one_or_none()


async def _mint_bind_token(session: AsyncSession, project: ProjectRow, pod_id: str) -> str | None:
    """Fresh pod bridge JWT — same mechanism + scope set as PodCommand launch."""
    try:
        from prodavan.application.modules.module_binding_service import ModuleBindingService

        module_ids = await ModuleBindingService(session).list_module_ids_for_project(project.id)
        scopes = build_launch_scopes(module_ids)
        token, _claims = await mint_pod_bridge_token(
            project_id=project.id,
            cabinet_id=project.cabinet_id,
            company_id=project.company_id,
            pod_id=pod_id,
            scopes=scopes,
        )
        return token
    except Exception as exc:
        logger.warning("project bind: bridge token mint failed project_id=%s: %s", project.id, exc)
        return None


async def _load_bind_env(session: AsyncSession, project: ProjectRow) -> dict[str, str]:
    try:
        pairs = await ContainerEnvLoader(session).load_for_project(project)
    except Exception as exc:
        logger.debug("project bind: env load failed project_id=%s: %s", project.id, exc)
        return {}
    return {str(k): str(v) for k, v in pairs}


async def bind_project_runtime(
    session: AsyncSession,
    project_id: str,
    *,
    pod_id: str | None = None,
    workspace_key: str | None = None,
    endpoint: RuntimeEndpoint | None = None,
    http_client: type[httpx.AsyncClient] = httpx.AsyncClient,
) -> bool:
    """Best-effort POST /v1/project/bind. Returns True on 2xx; never raises."""
    try:
        return await _bind(
            session,
            project_id,
            pod_id=pod_id,
            workspace_key=workspace_key,
            endpoint=endpoint,
            http_client=http_client,
        )
    except Exception as exc:
        logger.debug("project bind failed project_id=%s: %s", project_id, exc)
        return False


async def _bind(
    session: AsyncSession,
    project_id: str,
    *,
    pod_id: str | None,
    workspace_key: str | None,
    endpoint: RuntimeEndpoint | None,
    http_client: type[httpx.AsyncClient],
) -> bool:
    project = await session.get(ProjectRow, project_id)
    if project is None:
        return False
    pod: ProjectPodRow | None = None
    if not pod_id or not workspace_key:
        pod = await _resolve_live_pod(session, project_id)
    pod_id = pod_id or (pod.id if pod is not None else "")
    workspace_key = workspace_key or (
        str(pod.workspace_key) if pod is not None and pod.workspace_key else str(project.workspace_key or "")
    )
    if not pod_id:
        logger.debug("project bind: no live pod for project %s", project_id)
        return False
    if endpoint is None:
        endpoint = await resolve_runtime_endpoint(session, project_id)
    if endpoint is None:
        return False

    body: dict[str, Any] = {
        "project_id": project_id,
        "pod_id": pod_id,
        "workspace_key": workspace_key,
        "api_base_url": settings.pod_agent_runtime_api_base_url,
        "auth_token": await _mint_bind_token(session, project, pod_id),
        "env": await _load_bind_env(session, project),
    }
    url = f"{endpoint.base_url}/v1/project/bind"
    async with http_client(timeout=_BIND_TIMEOUT_S) as client:
        response = await client.post(url, json=body, headers=_bind_request_headers(endpoint))
    if response.status_code in (200, 201, 204):
        logger.info("project bind ok project_id=%s pod_id=%s", project_id, pod_id)
        return True
    if response.status_code == 404:
        logger.warning(
            "project bind: runtime has no /v1/project/bind (old image) project_id=%s",
            project_id,
        )
        return False
    logger.warning(
        "project bind failed project_id=%s status=%s body=%s",
        project_id,
        response.status_code,
        response.text[:200],
    )
    return False
