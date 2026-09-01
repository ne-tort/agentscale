"""Best-effort OpenClaw agent-bridge session bootstrap + send proxy (L03/L15)."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.config.settings import settings
from prodavan.domain.agent import FROZEN_EVENT_TYPES, AgentEvent, AgentEventType
from prodavan.domain.ai_keys import ApiKind
from prodavan.domain.pods import POD_TERMINAL_STATUSES
from prodavan.infrastructure.k8s.sandbox.client import K8sSandboxClient
from prodavan.infrastructure.persistence.models.projects import ProjectPodRow, ProjectRow

logger = logging.getLogger(__name__)

_BRIDGE_ADAPTER_KINDS = frozenset(
    {
        "platform_openclaw",
        "openclaw_sdk",
        "cursor_sdk",
        "codex_sdk",
        "claude_agent_sdk",
    }
)

_BRIDGE_SKIP_EVENT_TYPES = frozenset({"system_init", "ping"})
PRODAVAN_EVENTS_OWNER_HEADER = "X-Prodavan-Events-Owner"
PRODAVAN_EVENTS_OWNER_API = "api"


def _runtime_request_headers() -> dict[str, str]:
    headers = {PRODAVAN_EVENTS_OWNER_HEADER: PRODAVAN_EVENTS_OWNER_API}
    token = settings.pod_agent_runtime_token.strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def api_kind_to_bridge_adapter(api_kind: str) -> str:
    """Map Prodavan api_kind → OpenClaw bridge adapter_kind."""
    if api_kind == ApiKind.CURSOR_SDK:
        return "cursor_sdk"
    if api_kind == ApiKind.CODEX_SDK:
        return "codex_sdk"
    if api_kind == ApiKind.CLAUDE_AGENT_SDK:
        return "claude_agent_sdk"
    return "platform_openclaw"


def bridge_envelope_to_agent_event(envelope: dict) -> AgentEvent | None:
    """Map bridge SSE AgentEventEnvelope → platform AgentEvent (skip non-persistable)."""
    etype = str(envelope.get("type") or "")
    if etype in _BRIDGE_SKIP_EVENT_TYPES:
        return None
    if etype not in FROZEN_EVENT_TYPES:
        return None
    data = envelope.get("data")
    return AgentEvent.now(etype, data if isinstance(data, dict) else {})


@dataclass(frozen=True)
class BridgeSessionBootstrap:
    session_id: str
    prodavan_session_id: str
    adapter_kind: str
    model: str | None = None
    provider_key_id: str | None = None


class OpenClawBridgeBootstrap:
    """Register Prodavan agent session in Pod sidecar (identity map session ids)."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        k8s_client: K8sSandboxClient | None = None,
        http_client: type[httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        self._session = session
        self._k8s = k8s_client
        self._http_client = http_client

    async def register_session(
        self,
        *,
        project_id: str,
        payload: BridgeSessionBootstrap,
    ) -> bool:
        if not settings.pod_agent_runtime_enabled or not settings.pod_agent_runtime_bootstrap_enabled:
            return False
        if payload.adapter_kind not in _BRIDGE_ADAPTER_KINDS:
            return False

        pod_ip = await self._resolve_pod_ip_for_project(project_id)
        if not pod_ip:
            logger.debug("openclaw bootstrap: no pod ip for project %s", project_id)
            return False

        url = f"http://{pod_ip}:{settings.pod_agent_runtime_port}/v1/sessions"
        body = {
            "session_id": payload.session_id,
            "prodavan_session_id": payload.prodavan_session_id,
            "adapter_kind": payload.adapter_kind,
        }
        if payload.model:
            body["model"] = payload.model
        if payload.provider_key_id:
            body["provider_key_id"] = payload.provider_key_id

        try:
            async with self._http_client(timeout=5.0) as client:
                response = await client.post(url, json=body, headers=_runtime_request_headers())
            if response.status_code in (200, 201):
                logger.info(
                    "openclaw bootstrap: registered session %s on pod %s",
                    payload.session_id,
                    project_id,
                )
                return True
            logger.warning(
                "openclaw bootstrap: bridge returned %s for %s: %s",
                response.status_code,
                payload.session_id,
                response.text[:200],
            )
        except Exception as exc:
            logger.debug("openclaw bootstrap failed for %s: %s", payload.session_id, exc)
        return False

    async def iter_send_events(
        self,
        *,
        project_id: str,
        session_id: str,
        message: str,
    ) -> AsyncIterator[AgentEvent]:
        """Proxy send to Pod agent-runtime; yields normalized AgentEvent stream."""
        if not settings.pod_agent_runtime_enabled:
            return

        pod_ip = await self._resolve_pod_ip_for_project(project_id)
        if not pod_ip:
            logger.debug("openclaw send: no pod ip for project %s", project_id)
            return

        url = f"http://{pod_ip}:{settings.pod_agent_runtime_port}/v1/sessions/{session_id}/send"
        body: dict[str, str] = {"message": message}

        try:
            yielded = False
            async with self._http_client(timeout=None) as client:
                async with client.stream(
                    "POST",
                    url,
                    json=body,
                    headers=_runtime_request_headers(),
                ) as response:
                    if response.status_code >= 400:
                        text = await response.aread()
                        yield AgentEvent.now(
                            AgentEventType.ERROR,
                            {
                                "code": "BRIDGE_SEND_FAILED",
                                "message": text.decode("utf-8", errors="replace")[:500],
                                "retryable": response.status_code >= 500,
                            },
                        )
                        return
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw or raw == "[DONE]":
                            continue
                        try:
                            envelope = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(envelope, dict):
                            continue
                        event = bridge_envelope_to_agent_event(envelope)
                        if event is not None:
                            yielded = True
                            yield event
            if not yielded:
                yield AgentEvent.now(
                    AgentEventType.ERROR,
                    {
                        "code": "BRIDGE_EMPTY_STREAM",
                        "message": "bridge returned no agent events",
                        "retryable": True,
                    },
                )
        except Exception as exc:
            logger.warning("openclaw send proxy failed session=%s: %s", session_id, exc)
            yield AgentEvent.now(
                AgentEventType.ERROR,
                {"code": "BRIDGE_UNREACHABLE", "message": str(exc), "retryable": True},
            )

    async def _resolve_pod_ip_for_project(self, project_id: str) -> str | None:
        runtime_ref = await self._resolve_runtime_ref(project_id)
        if not runtime_ref:
            return None
        return await self._resolve_pod_ip(runtime_ref)

    async def _resolve_runtime_ref(self, project_id: str) -> str | None:
        q = await self._session.execute(
            select(ProjectPodRow.runtime_ref, ProjectRow.container_ref)
            .join(ProjectRow, ProjectRow.id == ProjectPodRow.project_id)
            .where(ProjectPodRow.project_id == project_id)
            .where(ProjectPodRow.status.notin_(tuple(POD_TERMINAL_STATUSES)))
            .order_by(ProjectPodRow.updated_at.desc())
            .limit(1)
        )
        row = q.first()
        if row is None:
            return None
        runtime_ref, container_ref = row
        return (runtime_ref or container_ref or "").strip() or None

    async def _resolve_pod_ip(self, runtime_ref: str) -> str | None:
        client = self._k8s or K8sSandboxClient(namespace=settings.pod_sandbox_namespace)
        if not client.available():
            return None
        snap = await client.get_pod(runtime_ref)
        if snap is None or not snap.ready or snap.phase != "Running":
            return None
        return snap.pod_ip

    async def resolve_approval(
        self,
        *,
        project_id: str,
        approval_id: str,
        decision: str,
    ) -> bool:
        """Forward HITL decision to agent-runtime."""
        if not settings.pod_agent_runtime_enabled:
            return False
        pod_ip = await self._resolve_pod_ip_for_project(project_id)
        if not pod_ip:
            return False
        bridge_decision = "allow" if decision == "approve" else "deny"
        url = f"http://{pod_ip}:{settings.pod_agent_runtime_port}/v1/approvals/{approval_id}"
        try:
            async with self._http_client(timeout=10.0) as client:
                response = await client.post(
                    url,
                    json={"decision": bridge_decision},
                    headers=_runtime_request_headers(),
                )
            return response.status_code in (200, 201)
        except Exception as exc:
            logger.debug("approval forward failed %s: %s", approval_id, exc)
            return False

    async def get_sidechain_transcript(
        self,
        *,
        project_id: str,
        session_id: str,
        tool_use_id: str,
    ) -> dict | None:
        if not settings.pod_agent_runtime_enabled:
            return None
        pod_ip = await self._resolve_pod_ip_for_project(project_id)
        if not pod_ip:
            return None
        url = (
            f"http://{pod_ip}:{settings.pod_agent_runtime_port}"
            f"/v1/sessions/{session_id}/sidechains/{tool_use_id}/transcript"
        )
        try:
            async with self._http_client(timeout=10.0) as client:
                response = await client.get(url, headers=_runtime_request_headers())
            if response.status_code >= 400:
                return None
            body = response.json()
            return body if isinstance(body, dict) else None
        except Exception as exc:
            logger.debug("sidechain transcript failed session=%s: %s", session_id, exc)
            return None

    async def fork_session(
        self,
        *,
        project_id: str,
        source_session_id: str,
        new_session_id: str,
    ) -> dict | None:
        if not settings.pod_agent_runtime_enabled:
            return None
        pod_ip = await self._resolve_pod_ip_for_project(project_id)
        if not pod_ip:
            return None
        url = f"http://{pod_ip}:{settings.pod_agent_runtime_port}/v1/sessions/{source_session_id}/fork"
        try:
            async with self._http_client(timeout=10.0) as client:
                response = await client.post(
                    url,
                    json={"session_id": new_session_id},
                    headers=_runtime_request_headers(),
                )
            if response.status_code >= 400:
                return None
            body = response.json()
            return body if isinstance(body, dict) else None
        except Exception as exc:
            logger.debug("fork session failed %s: %s", source_session_id, exc)
            return None
