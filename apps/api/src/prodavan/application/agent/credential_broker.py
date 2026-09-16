"""Pod credential broker — lease AI keys into agent-runtime memory (no env, no read-back)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.agent.openclaw_bridge import OpenClawBridgeBootstrap
from prodavan.application.ai_keys.audit_service import AiKeyAuditService
from prodavan.application.ai_keys.service import AiKeysService
from prodavan.config.settings import settings
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.k8s.sandbox.client import K8sSandboxClient
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow

logger = logging.getLogger(__name__)

_DEFAULT_LEASE_TTL_SEC = 3600

# Audit event types for credential leases (API-P2a). The secret itself is
# never written to the audit trail — AiKeyAuditService._sanitize_detail redacts
# any ``secret`` key, and we do not put the secret into detail at all.
LEASE_CREATED = "lease.created"
LEASE_PUSHED = "lease.pushed"
LEASE_REVOKED = "lease.revoked"
LEASE_PUSH_FAILED = "lease.push_failed"

_SYSTEM_PRINCIPAL = Principal(sub="system:credential-broker", roles=frozenset())


@dataclass(frozen=True)
class CredentialHandle:
    key_id: str
    name: str
    provider: str
    api_kind: str


class AgentCredentialBroker:
    def __init__(
        self,
        session: AsyncSession,
        *,
        k8s_client: K8sSandboxClient | None = None,
        http_client: type[httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        self._session = session
        self._keys = AiKeysService(session)
        self._audit = AiKeyAuditService(session)
        self._k8s = k8s_client
        self._http_client = http_client

    async def list_handles_for_pod(self, *, pod_id: str) -> list[CredentialHandle]:
        project = await self._require_pod_project(pod_id)
        items = await self._keys.list_available_keys_for_project(project=project)
        out: list[CredentialHandle] = []
        for item in items:
            kid = item.get("id")
            if not isinstance(kid, str):
                continue
            out.append(
                CredentialHandle(
                    key_id=kid,
                    name=str(item.get("name") or kid),
                    provider=str(item.get("provider") or ""),
                    api_kind=str(item.get("api_kind") or ""),
                ),
            )
        return out

    async def create_lease(
        self,
        *,
        pod_id: str,
        key_id: str,
        ttl_sec: int = _DEFAULT_LEASE_TTL_SEC,
        principal: Principal | None = None,
    ) -> dict:
        """Return lease metadata + secret once (pod auth only).

        Audit API-P2a: every lease creation is recorded in the AI key audit
        trail (``ai_key_audit_events``) with lease_id, pod_id, project_id and
        ttl — but never the secret itself.
        """
        project = await self._require_pod_project(pod_id)
        await self._keys.require_key_available_for_project(project=project, key_id=key_id)
        secret = await self._keys.resolve_secret_for_key(key_id)
        lease_id = f"lease_{uuid.uuid4().hex[:16]}"
        ttl = max(60, min(int(ttl_sec), 86_400))
        await self._audit.record(
            event_type=LEASE_CREATED,
            key_id=key_id,
            principal=principal or _SYSTEM_PRINCIPAL,
            detail={
                "lease_id": lease_id,
                "pod_id": pod_id,
                "project_id": project.id,
                "ttl_sec": ttl,
            },
        )
        return {
            "lease_id": lease_id,
            "key_id": key_id,
            "ttl_sec": ttl,
            "secret": secret,
        }

    async def push_lease_to_runtime(
        self,
        *,
        project_id: str,
        key_id: str,
        ttl_sec: int = _DEFAULT_LEASE_TTL_SEC,
        principal: Principal | None = None,
    ) -> bool:
        """API → runtime: install lease in agent-runtime memory.

        Audit API-P2a: every push is recorded (success and failure) in the AI
        key audit trail. The secret is never written to the audit row.
        """
        if not settings.pod_agent_runtime_enabled:
            return False
        project = await self._session.get(ProjectRow, project_id)
        if project is None:
            return False
        try:
            await self._keys.require_key_available_for_project(project=project, key_id=key_id)
            secret = await self._keys.resolve_secret_for_key(key_id)
        except Exception as exc:
            logger.debug("credential push skipped key=%s: %s", key_id, exc)
            return False

        pod_ip = await self._resolve_pod_ip_for_project(project_id)
        if not pod_ip:
            return False

        lease_id = f"lease_{uuid.uuid4().hex[:16]}"
        url = f"http://{pod_ip}:{settings.pod_agent_runtime_port}/v1/credentials/leases"
        body = {
            "lease_id": lease_id,
            "key_id": key_id,
            "secret": secret,
            "ttl_sec": max(60, min(int(ttl_sec), 86_400)),
        }
        headers = _runtime_request_headers()
        actor = principal or _SYSTEM_PRINCIPAL
        try:
            async with self._http_client(timeout=5.0) as client:
                response = await client.post(url, json=body, headers=headers)
            if response.status_code in (200, 201):
                logger.info("credential lease pushed key=%s project=%s", key_id, project_id)
                await self._audit.record(
                    event_type=LEASE_PUSHED,
                    key_id=key_id,
                    principal=actor,
                    detail={
                        "lease_id": lease_id,
                        "project_id": project_id,
                        "ttl_sec": body["ttl_sec"],
                    },
                )
                return True
            logger.warning(
                "credential push failed status=%s body=%s",
                response.status_code,
                response.text[:200],
            )
            await self._audit.record(
                event_type=LEASE_PUSH_FAILED,
                key_id=key_id,
                principal=actor,
                detail={
                    "lease_id": lease_id,
                    "project_id": project_id,
                    "status": response.status_code,
                    "body": response.text[:200],
                },
            )
        except Exception as exc:
            logger.debug("credential push unreachable project=%s: %s", project_id, exc)
            await self._audit.record(
                event_type=LEASE_PUSH_FAILED,
                key_id=key_id,
                principal=actor,
                detail={
                    "lease_id": lease_id,
                    "project_id": project_id,
                    "error": str(exc)[:256],
                },
            )
        return False

    async def revoke_lease_for_pod(
        self,
        *,
        pod_id: str,
        lease_id: str,
        principal: Principal | None = None,
    ) -> bool:
        """Revoke runtime lease; resolves project from pod row."""
        pod = await self._session.get(ProjectPodRow, pod_id)
        if pod is None or not pod.project_id:
            return False
        return await self.revoke_runtime_lease(
            project_id=pod.project_id, lease_id=lease_id, principal=principal
        )

    async def revoke_runtime_lease(
        self,
        *,
        project_id: str,
        lease_id: str,
        principal: Principal | None = None,
    ) -> bool:
        if not settings.pod_agent_runtime_enabled:
            return False
        pod_ip = await self._resolve_pod_ip_for_project(project_id)
        if not pod_ip:
            return False
        url = f"http://{pod_ip}:{settings.pod_agent_runtime_port}/v1/credentials/leases/{lease_id}"
        try:
            async with self._http_client(timeout=10.0) as client:
                response = await client.delete(url, headers=_runtime_request_headers())
            ok = response.status_code in (200, 204, 404)
            await self._audit.record(
                event_type=LEASE_REVOKED,
                key_id=None,
                principal=principal or _SYSTEM_PRINCIPAL,
                detail={
                    "lease_id": lease_id,
                    "project_id": project_id,
                    "status": response.status_code,
                    "ok": ok,
                },
            )
            return ok
        except Exception as exc:
            logger.debug("credential revoke unreachable project=%s: %s", project_id, exc)
            await self._audit.record(
                event_type=LEASE_REVOKED,
                key_id=None,
                principal=principal or _SYSTEM_PRINCIPAL,
                detail={
                    "lease_id": lease_id,
                    "project_id": project_id,
                    "error": str(exc)[:256],
                    "ok": False,
                },
            )
            return False

    async def _require_pod_project(self, pod_id: str) -> ProjectRow:
        pod = await self._session.get(ProjectPodRow, pod_id)
        if pod is None or not pod.project_id:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="pod not found")
        project = await self._session.get(ProjectRow, pod.project_id)
        if project is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="project not found")
        return project

    async def _resolve_pod_ip_for_project(self, project_id: str) -> str | None:
        bridge = OpenClawBridgeBootstrap(self._session, k8s_client=self._k8s)
        return await bridge._resolve_pod_ip_for_project(project_id)


def _runtime_request_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    token = settings.pod_agent_runtime_token.strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers
