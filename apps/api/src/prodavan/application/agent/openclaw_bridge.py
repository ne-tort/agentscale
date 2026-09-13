"""Best-effort OpenClaw agent-bridge session bootstrap + send proxy (L03/L15)."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.agent.runtime_model import sanitize_runtime_model
from prodavan.config.settings import settings
from prodavan.domain.agent import FROZEN_EVENT_TYPES, PLATFORM_STREAM_EVENT_TYPES, AgentEvent, AgentEventType
from prodavan.domain.agent.errors import POD_NOT_RUNNING
from prodavan.domain.ai_keys import ApiKind
from prodavan.infrastructure.k8s.sandbox.client import K8sSandboxClient

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

_BRIDGE_SKIP_EVENT_TYPES = frozenset({"ping"})
_STUB_TEXT_PREFIXES = (
    "[cursor-sdk stub]",
    "[claude-agent-sdk stub]",
    "[codex-sdk stub]",
)
PRODAVAN_EVENTS_OWNER_HEADER = "X-Prodavan-Events-Owner"
PRODAVAN_EVENTS_OWNER_API = "api"


def _explicit_bridge_model(model: str | None) -> str | None:
    return sanitize_runtime_model(model)


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
    if etype not in FROZEN_EVENT_TYPES and etype not in PLATFORM_STREAM_EVENT_TYPES:
        return None
    raw_data = envelope.get("data")
    data = dict(raw_data) if isinstance(raw_data, dict) else {}
    parent_id = envelope.get("parent_tool_use_id")
    if parent_id is not None and "parent_tool_use_id" not in data:
        data["parent_tool_use_id"] = parent_id
    return AgentEvent.now(etype, data)


def bridge_envelope_is_stub(envelope: dict) -> bool:
    """Detect legacy/test stub responses from agent-runtime adapters."""
    etype = str(envelope.get("type") or "")
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    if etype == "system_init" and data.get("stub") is True:
        return True
    if etype == "text_delta":
        text = str(data.get("text") or "")
        return any(text.startswith(prefix) for prefix in _STUB_TEXT_PREFIXES)
    return False


def bridge_stub_error_event() -> AgentEvent:
    return AgentEvent.now(
        AgentEventType.ERROR,
        {
            "code": "AGENT_STUB_RESPONSE",
            "message": "agent runtime returned a stub response; redeploy agent-runtime image",
            "retryable": False,
        },
    )


@dataclass(frozen=True)
class BridgeSessionBootstrap:
    session_id: str
    prodavan_session_id: str
    adapter_kind: str
    model: str | None = None
    provider_key_id: str | None = None
    adapter_state: dict | None = None


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
        bridge_model = _explicit_bridge_model(payload.model)
        if bridge_model:
            body["model"] = bridge_model
        if payload.provider_key_id:
            body["provider_key_id"] = payload.provider_key_id

        try:
            async with self._http_client(timeout=5.0) as client:
                response = await client.post(url, json=body, headers=_runtime_request_headers())
            if response.status_code in (200, 201):
                if payload.adapter_state:
                    await self._patch_adapter_state(
                        pod_ip=pod_ip,
                        session_id=payload.session_id,
                        adapter_state=payload.adapter_state,
                        provider_key_id=payload.provider_key_id,
                        model=_explicit_bridge_model(payload.model),
                    )
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

    async def _patch_adapter_state(
        self,
        *,
        pod_ip: str,
        session_id: str,
        adapter_state: dict,
        provider_key_id: str | None = None,
        model: str | None = None,
    ) -> bool:
        """PATCH adapter_state; always re-send key/model when known.

        Older agent-runtime builds spread undefined patch fields and wipe
        ``providerKeyId`` / ``model`` if only ``adapter_state`` is sent.
        """
        url = f"http://{pod_ip}:{settings.pod_agent_runtime_port}/v1/sessions/{session_id}"
        body: dict[str, object] = {"adapter_state": adapter_state}
        if provider_key_id:
            body["provider_key_id"] = provider_key_id
        if model:
            body["model"] = model
        try:
            async with self._http_client(timeout=5.0) as client:
                response = await client.patch(
                    url,
                    json=body,
                    headers=_runtime_request_headers(),
                )
            return response.status_code in (200, 204)
        except Exception as exc:
            logger.debug("openclaw adapter_state patch failed session=%s: %s", session_id, exc)
            return False

    async def sync_adapter_state_for_session(
        self,
        *,
        project_id: str,
        session_id: str,
    ) -> dict | None:
        """Best-effort pull adapterState from bridge list after send."""
        if not settings.pod_agent_runtime_enabled:
            return None
        pod_ip = await self._resolve_pod_ip_for_project(project_id)
        if not pod_ip:
            return None
        url = f"http://{pod_ip}:{settings.pod_agent_runtime_port}/v1/sessions"
        try:
            async with self._http_client(timeout=5.0) as client:
                response = await client.get(url, headers=_runtime_request_headers())
            if response.status_code >= 400:
                return None
            payload = response.json()
            sessions = payload.get("sessions") if isinstance(payload, dict) else None
            if not isinstance(sessions, list):
                return None
            for item in sessions:
                if not isinstance(item, dict):
                    continue
                sid = str(item.get("sessionId") or item.get("session_id") or "")
                if sid != session_id:
                    continue
                state = item.get("adapterState") or item.get("adapter_state")
                return state if isinstance(state, dict) else None
        except Exception as exc:
            logger.debug("openclaw sync adapter_state failed session=%s: %s", session_id, exc)
        return None

    async def iter_send_events(
        self,
        *,
        project_id: str,
        session_id: str,
        message: str,
        model: str | None = None,
        bootstrap: BridgeSessionBootstrap | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Proxy send to Pod agent-runtime; yields normalized AgentEvent stream."""
        if not settings.pod_agent_runtime_enabled:
            return

        retried = False
        while True:
            recoverable = False
            pod_ip = await self._resolve_pod_ip_for_project(project_id)
            if not pod_ip:
                logger.debug("openclaw send: no pod ip for project %s", project_id)
                yield AgentEvent.now(
                    AgentEventType.ERROR,
                    {
                        "code": POD_NOT_RUNNING,
                        "message": "pod is not running or not ready",
                        "retryable": False,
                    },
                )
                return

            if bootstrap is not None and bootstrap.provider_key_id:
                # Heal sessions wiped by older bridge PATCH (undefined fields cleared key/model).
                await self._patch_adapter_state(
                    pod_ip=pod_ip,
                    session_id=session_id,
                    adapter_state=bootstrap.adapter_state
                    if isinstance(bootstrap.adapter_state, dict)
                    else {},
                    provider_key_id=bootstrap.provider_key_id,
                    model=_explicit_bridge_model(model)
                    or _explicit_bridge_model(bootstrap.model),
                )

            async for event in self._stream_send(
                pod_ip=pod_ip,
                session_id=session_id,
                message=message,
                model=model,
            ):
                if (
                    not retried
                    and bootstrap is not None
                    and event.type == AgentEventType.ERROR
                    and isinstance(event.data, dict)
                    and event.data.get("code")
                    in {"BRIDGE_SESSION_NOT_FOUND", "BRIDGE_UNREACHABLE", "BRIDGE_EMPTY_STREAM"}
                ):
                    recoverable = True
                    break
                yield event
                if event.type in {AgentEventType.ERROR, AgentEventType.DONE}:
                    return
            if recoverable and not retried and bootstrap is not None:
                retried = True
                if await self.register_session(project_id=project_id, payload=bootstrap):
                    continue
                yield AgentEvent.now(
                    AgentEventType.ERROR,
                    {
                        "code": "BRIDGE_SESSION_NOT_FOUND",
                        "message": "agent session missing in pod after restart",
                        "retryable": False,
                    },
                )
            return

    async def _stream_send(
        self,
        *,
        pod_ip: str,
        session_id: str,
        message: str,
        model: str | None,
    ) -> AsyncIterator[AgentEvent]:
        url = f"http://{pod_ip}:{settings.pod_agent_runtime_port}/v1/sessions/{session_id}/send"
        body: dict[str, str] = {"message": message}
        bridge_model = sanitize_runtime_model(model)
        if bridge_model:
            body["model"] = bridge_model

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
                        body_text = text.decode("utf-8", errors="replace")
                        code = "BRIDGE_SEND_FAILED"
                        if response.status_code == 404 and "session not found" in body_text.lower():
                            code = "BRIDGE_SESSION_NOT_FOUND"
                        yield AgentEvent.now(
                            AgentEventType.ERROR,
                            {
                                "code": code,
                                "message": body_text[:500],
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
                        if bridge_envelope_is_stub(envelope):
                            yield bridge_stub_error_event()
                            return
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
        from prodavan.application.pod_service.query import PodQuery

        view = await PodQuery(self._session).runtime_view(project_id)
        if view is None:
            return None
        if str(view.get("observed_state") or "") != "running":
            return None
        ref = str(view.get("k8s_pod_name") or view.get("runtime_ref") or "").strip()
        if not ref or ref.startswith("object-ws:"):
            return None
        return ref

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
