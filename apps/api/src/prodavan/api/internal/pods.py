"""Internal pod-scoped API (agent-runtime credential broker)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.api.agent_auth import AgentAuth, get_agent_auth
from prodavan.application.agent.credential_broker import AgentCredentialBroker
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.database import get_db_session

router = APIRouter(prefix="/internal/pods", tags=["internal-pods"])


class LeaseBody(BaseModel):
    ttl_sec: int = Field(default=3600, ge=60, le=86_400)


def _require_pod_agent(auth: AgentAuth) -> None:
    if not auth.pod_agent:
        raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="pod agent token required")


@router.get("/{pod_id}/credentials")
async def list_pod_credentials(
    pod_id: str,
    auth: Annotated[AgentAuth, Depends(get_agent_auth)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    _require_pod_agent(auth)
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
    _require_pod_agent(auth)
    broker = AgentCredentialBroker(session)
    return await broker.create_lease(pod_id=pod_id, key_id=key_id, ttl_sec=body.ttl_sec)


@router.delete("/{pod_id}/credentials/leases/{lease_id}", status_code=204)
async def revoke_pod_credential_lease(
    pod_id: str,
    lease_id: str,
    auth: Annotated[AgentAuth, Depends(get_agent_auth)],
) -> None:
    _require_pod_agent(auth)
    _ = pod_id, lease_id
    # Leases live in runtime memory; revocation is runtime-local (DELETE /v1/credentials/leases on pod).
