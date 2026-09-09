"""Internal pod-scoped API (credential broker + workspace hydrate archive)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.api.agent_auth import AgentAuth, get_agent_auth, require_bridge_pod
from prodavan.application.agent.credential_broker import AgentCredentialBroker
from prodavan.application.pod_identity.bridge import SCOPE_INTERNAL_CREDENTIALS
from prodavan.application.pod_service.workspace_tar_download import download_workspace_tar
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.database import get_db_session
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow

router = APIRouter(prefix="/internal/pods", tags=["internal-pods"])


class LeaseBody(BaseModel):
    ttl_sec: int = Field(default=3600, ge=60, le=86_400)


def _require_pod_bridge(auth: AgentAuth, pod_id: str) -> None:
    if not auth.pod_agent or auth.bridge is None:
        raise AppError(
            code="FORBIDDEN",
            title="Forbidden",
            status=403,
            detail="pod bridge JWT required",
        )
    require_bridge_pod(auth.bridge, pod_id)


@router.get("/{pod_id}/workspace-archive")
async def download_pod_workspace_archive(
    pod_id: str,
    auth: Annotated[AgentAuth, Depends(get_agent_auth)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    """API-mediated hydrate: Pod initContainer fetches tar (no MinIO egress)."""
    _require_pod_bridge(auth, pod_id)
    assert auth.bridge is not None
    pod = await session.get(ProjectPodRow, pod_id)
    if pod is None:
        raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="pod not found")
    if pod.project_id and pod.project_id != auth.bridge.project_id:
        raise AppError(
            code="FORBIDDEN",
            title="Forbidden",
            status=403,
            detail="pod bridge project mismatch",
        )
    workspace_key = (pod.workspace_key or "").strip()
    if not workspace_key:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="pod workspace_key missing",
        )
    try:
        data = download_workspace_tar(workspace_key=workspace_key)
    except RuntimeError as exc:
        raise AppError(
            code="PAYLOAD_TOO_LARGE",
            title="Payload Too Large",
            status=413,
            detail=str(exc),
        ) from exc
    return Response(
        content=data,
        media_type="application/x-tar",
        headers={
            "Content-Disposition": f'attachment; filename="{workspace_key}.tar"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/{pod_id}/credentials")
async def list_pod_credentials(
    pod_id: str,
    auth: Annotated[AgentAuth, Depends(get_agent_auth)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    _require_pod_bridge(auth, pod_id)
    assert auth.bridge is not None
    auth.bridge.require_scope(SCOPE_INTERNAL_CREDENTIALS)
    broker = AgentCredentialBroker(session)
    handles = await broker.list_handles_for_pod(pod_id=pod_id)
    return {
        "items": [
            {
                "key_id": h.key_id,
                "name": h.name,
                "provider": h.provider,
                "api_kind": h.api_kind,
            }
            for h in handles
        ],
    }


@router.post("/{pod_id}/credentials/{key_id}/lease", status_code=201)
async def create_pod_credential_lease(
    pod_id: str,
    key_id: str,
    body: LeaseBody,
    auth: Annotated[AgentAuth, Depends(get_agent_auth)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    _require_pod_bridge(auth, pod_id)
    assert auth.bridge is not None
    auth.bridge.require_scope(SCOPE_INTERNAL_CREDENTIALS)
    broker = AgentCredentialBroker(session)
    return await broker.create_lease(pod_id=pod_id, key_id=key_id, ttl_sec=body.ttl_sec)


@router.delete("/{pod_id}/credentials/leases/{lease_id}", status_code=204)
async def revoke_pod_credential_lease(
    pod_id: str,
    lease_id: str,
    auth: Annotated[AgentAuth, Depends(get_agent_auth)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    _require_pod_bridge(auth, pod_id)
    assert auth.bridge is not None
    auth.bridge.require_scope(SCOPE_INTERNAL_CREDENTIALS)
    broker = AgentCredentialBroker(session)
    if not await broker.revoke_lease_for_pod(pod_id=pod_id, lease_id=lease_id):
        raise AppError(
            code="REVOKE_FAILED",
            title="Bad Gateway",
            status=502,
            detail="could not revoke credential lease",
        )
