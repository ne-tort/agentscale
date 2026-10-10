"""Best-effort OpenClaw agent-bridge session bootstrap + send proxy (L03/L15).

Transport: all agent-runtime traffic is addressed via RuntimeEndpoint
(application/agent/runtime_transport.py) — direct pod-IP in ``k8s`` mode, the
agent-sandbox sandbox-router (``X-Sandbox-*`` headers) in ``sandbox`` mode,
where ``runtime_ref`` is a SandboxClaim name and the backing Sandbox (pod) can
be re-adopted at any time, so pod IPs are never addressed directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.agent.adapter_kinds import (
    BRIDGE_ADAPTER_KINDS,
    api_kind_to_bridge_adapter,  # noqa: F401 — re-export (session_service, live_service import it from here)
)
from prodavan.application.agent.project_bind import bind_project_runtime
from prodavan.application.agent.runtime_auth import runtime_auth_headers
from prodavan.application.agent.runtime_model import sanitize_runtime_model
from prodavan.application.agent.runtime_transport import (
    RuntimeEndpoint,
    invalidate_sandbox_name_cache,
    resolve_runtime_endpoint,
    runtime_mode,
)
from prodavan.config.settings import settings
from prodavan.domain.agent import FROZEN_EVENT_TYPES, PLATFORM_STREAM_EVENT_TYPES, AgentEvent, AgentEventType
from prodavan.domain.agent.errors import POD_NOT_RUNNING

if TYPE_CHECKING:
    from prodavan.infrastructure.k8s.sandbox.client import K8sSandboxClient

logger = logging.getLogger(__name__)

_BRIDGE_SKIP_EVENT_TYPES = frozenset({"ping"})
_STUB_TEXT_PREFIXES = (
    "[cursor-sdk stub]",
    "[claude-agent-sdk stub]",
    "[codex-sdk stub]",
)
PRODAVAN_EVENTS_OWNER_HEADER = "X-Prodavan-Events-Owner"
PRODAVAN_EVENTS_OWNER_API = "api"

# Send-side errors after which one re-resolve + re-register + retry is allowed.
# In sandbox mode a router 404/410 usually means the Sandbox was re-adopted
# under a new name; the retry loop re-resolves the endpoint from scratch.
_RECOVERABLE_SEND_ERROR_CODES = frozenset(
    {
        "BRIDGE_SESSION_NOT_FOUND",
        "BRIDGE_UNREACHABLE",
        "BRIDGE_EMPTY_STREAM",
        "BRIDGE_HYDRATING",
        "BRIDGE_TIMEOUT",
    }
)

# Streaming send timeouts (Wave 5): the old ``timeout=None`` let a wedged
# runtime (hung LLM call, stalled transcript fetch) hold the API→client SSE
# open forever with zero events — the user saw an eternal "typing". The read
# budget bounds *silence between bytes*: the runtime's 15s SSE pings reset
# it, so long agent turns are NOT cut — only true silence is.
_SEND_CONNECT_TIMEOUT_SEC = 10.0
_SEND_READ_TIMEOUT_SEC = 90.0
_SEND_WRITE_TIMEOUT_SEC = 30.0

# register_session 409-hydrating: the runtime is restoring its workspace
# from the bind payload; one retry after this delay, then normal semantics.
_HYDRATING_RETRY_DELAY_SEC = 5.0
# ensure_recent_bind: Redis mark TTL — half of the default bridge-JWT TTL
# (24h) so the runtime receives a fresh token well before expiry.
_BIND_MARK_TTL_SEC = 12 * 60 * 60
# Redis-unavailable degradation: in-memory throttle so binds run at most
# this often per project per process instead of on every send.
_BIND_FALLBACK_INTERVAL_SEC = 300.0
# Bounded fallback-throttle map (LRU eviction): an unbounded dict would leak
# project ids for the whole process lifetime.
_BIND_ATTEMPT_CACHE_MAX = 1024
_last_bind_attempt: OrderedDict[str, float] = OrderedDict()


def _bind_attempt_throttled(throttle_key: str, now: float) -> bool:
    """Record a bind attempt; True when the previous attempt is too recent."""
    last = _last_bind_attempt.get(throttle_key)
    if last is not None and now - last < _BIND_FALLBACK_INTERVAL_SEC:
        return True
    _last_bind_attempt[throttle_key] = now
    _last_bind_attempt.move_to_end(throttle_key)
    while len(_last_bind_attempt) > _BIND_ATTEMPT_CACHE_MAX:
        _last_bind_attempt.popitem(last=False)
    return False


def _is_hydrating_conflict(response: httpx.Response | None) -> bool:
    """409 whose body says the runtime is hydrating (workspace restore)."""
    if response is None or response.status_code != 409:
        return False
    try:
        return "hydrating" in (response.text or "").lower()
    except Exception:  # noqa: BLE001 - body may be unreadable
        return False


async def ensure_recent_bind(
    session: AsyncSession,
    project_id: str,
    *,
    endpoint: RuntimeEndpoint | None = None,
    http_client: type[httpx.AsyncClient] = httpx.AsyncClient,
) -> None:
    """Re-bind the agent-runtime when the pod bridge JWT may be stale.

    The bridge JWT lives ~24h while a sandbox lives for days; once it
    expires, runtime→API callbacks start failing 401. A Redis mark
    (``prodavan:bind:<project_id>[:<sandbox_name>]``, TTL 12h) records the
    last bind; when
    it is absent the idempotent ``POST /v1/project/bind`` is re-run (it
    mints a fresh token and the runtime swaps it in). Redis-unavailable
    degradation: an in-memory throttle keeps binds at most once per
    ``_BIND_FALLBACK_INTERVAL_SEC`` per process instead of every send.
    """
    from prodavan.core.infra.cache import cache_get, cache_key, cache_set

    # The mark is keyed by the CURRENT sandbox identity: after a re-adoption
    # the claim points at a fresh warm pod that has never seen this project,
    # so a bind recorded for the previous sandbox must not suppress a re-bind.
    sandbox_name = ""
    if endpoint is not None:
        sandbox_name = str((endpoint.headers or {}).get("X-Sandbox-Id") or "").strip()
    key = cache_key("bind", project_id, sandbox_name)
    try:
        if await cache_get(key) is not None:
            return
    except Exception:  # noqa: BLE001 - cache helpers degrade, never raise
        pass
    if _bind_attempt_throttled(key, time.monotonic()):
        return
    try:
        ok = await bind_project_runtime(
            session,
            project_id,
            endpoint=endpoint,
            http_client=http_client,
        )
        if ok:
            # Mark only a CONFIRMED bind — a failed bind must not suppress
            # the next attempt for the whole 12h mark TTL.
            await cache_set(key, "1", ttl_sec=_BIND_MARK_TTL_SEC)
    except Exception as exc:  # noqa: BLE001 - best-effort by contract
        logger.debug("ensure_recent_bind failed project_id=%s: %s", project_id, exc)


def _explicit_bridge_model(model: str | None) -> str | None:
    return sanitize_runtime_model(model)


def _runtime_request_headers(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    headers = {PRODAVAN_EVENTS_OWNER_HEADER: PRODAVAN_EVENTS_OWNER_API}
    headers.update(runtime_auth_headers())
    if extra:
        headers.update(extra)
    return headers


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
        if payload.adapter_kind not in BRIDGE_ADAPTER_KINDS:
            return False

        endpoint = await self._resolve_endpoint_for_project(project_id)
        if endpoint is None:
            logger.debug("openclaw bootstrap: no runtime endpoint for project %s", project_id)
            return False

        # Post-Ready identity bind BEFORE session registration: in sandbox mode
        # the runtime may be a freshly adopted warm pod that has never seen this
        # project. Idempotent + 404-tolerant; never blocks registration.
        # ensure_recent_bind re-pushes identity (with a fresh bridge JWT)
        # when the last bind is older than half the JWT TTL.
        await ensure_recent_bind(
            self._session,
            project_id,
            endpoint=endpoint,
            http_client=self._http_client,
        )

        url = f"{endpoint.base_url}/v1/sessions"
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
            response = await self._post_session_registration(endpoint, url, body)
            if _is_hydrating_conflict(response):
                # 409 while the runtime self-hydrates from the bind payload:
                # one retry after a short delay, then normal semantics.
                logger.info(
                    "openclaw bootstrap: bridge hydrating, retry in %.0fs session=%s",
                    _HYDRATING_RETRY_DELAY_SEC,
                    payload.session_id,
                )
                await asyncio.sleep(_HYDRATING_RETRY_DELAY_SEC)
                response = await self._post_session_registration(endpoint, url, body)
            if response.status_code in (200, 201):
                if payload.adapter_state:
                    await self._patch_adapter_state(
                        endpoint=endpoint,
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

    async def _post_session_registration(
        self,
        endpoint: RuntimeEndpoint,
        url: str,
        body: dict,
    ) -> httpx.Response:
        """Single POST /v1/sessions attempt (no hydrating-retry logic)."""
        async with self._http_client(timeout=5.0) as client:
            return await client.post(
                url, json=body, headers=_runtime_request_headers(endpoint.headers)
            )

    async def _patch_adapter_state(
        self,
        *,
        endpoint: RuntimeEndpoint,
        session_id: str,
        adapter_state: dict,
        provider_key_id: str | None = None,
        model: str | None = None,
    ) -> bool:
        """PATCH adapter_state; always re-send key/model when known.

        Older agent-runtime builds spread undefined patch fields and wipe
        ``providerKeyId`` / ``model`` if only ``adapter_state`` is sent.
        """
        url = f"{endpoint.base_url}/v1/sessions/{session_id}"
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
                    headers=_runtime_request_headers(endpoint.headers),
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
        endpoint = await self._resolve_endpoint_for_project(project_id)
        if endpoint is None:
            return None
        url = f"{endpoint.base_url}/v1/sessions"
        try:
            async with self._http_client(timeout=5.0) as client:
                response = await client.get(url, headers=_runtime_request_headers(endpoint.headers))
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
        images: list[dict] | None = None,
        model: str | None = None,
        bootstrap: BridgeSessionBootstrap | None = None,
        endpoint: RuntimeEndpoint | None = None,
        retry: dict | None = None,
        max_turns: int | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Proxy send to Pod agent-runtime; yields normalized AgentEvent stream.

        ``endpoint``: pre-resolved endpoint for the first attempt (the send
        hot path already resolved it for the lease push) — saves a duplicate
        runtime_view + claim status fetch per message. The recoverable-error
        retry still re-resolves from scratch.
        ``retry``: SendRetryPolicy send-body fields (chat reconnect policy)
        — forwarded on every attempt.
        ``images``: vision images on the new user turn ({mime, data_base64})
        — forwarded as send-body ``images`` (SendRequestSchema validates).
        ``max_turns``: лимит шагов модели на этот ход (настройка чата).
        None — не отправляем вовсе, тогда действует значение из
        `.prodavan/config.yaml` (по умолчанию «без ограничений»).
        """
        if not settings.pod_agent_runtime_enabled:
            return

        retried = False
        pending_endpoint = endpoint
        while True:
            recoverable = False
            if pending_endpoint is not None:
                endpoint = pending_endpoint
                pending_endpoint = None
            else:
                endpoint = await self._resolve_endpoint_for_project(project_id)
            if endpoint is None:
                logger.debug("openclaw send: no runtime endpoint for project %s", project_id)
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
                # TODO(F8): drop this pre-send PATCH once all deployed runtimes
                # persist key/model — it costs one extra runtime call per send.
                # Heal sessions wiped by older bridge PATCH (undefined fields cleared key/model).
                await self._patch_adapter_state(
                    endpoint=endpoint,
                    session_id=session_id,
                    adapter_state=bootstrap.adapter_state
                    if isinstance(bootstrap.adapter_state, dict)
                    else {},
                    provider_key_id=bootstrap.provider_key_id,
                    model=_explicit_bridge_model(model)
                    or _explicit_bridge_model(bootstrap.model),
                )

            async for event in self._stream_send(
                endpoint=endpoint,
                project_id=project_id,
                session_id=session_id,
                message=message,
                images=images,
                model=model,
                retry=retry,
                max_turns=max_turns,
            ):
                if (
                    not retried
                    and event.type == AgentEventType.ERROR
                    and isinstance(event.data, dict)
                    and event.data.get("code") in _RECOVERABLE_SEND_ERROR_CODES
                    and (bootstrap is not None or runtime_mode() == "sandbox")
                ):
                    recoverable = True
                    break
                yield event
                if event.type in {AgentEventType.ERROR, AgentEventType.DONE}:
                    return
            if recoverable and not retried:
                retried = True
                if bootstrap is not None:
                    # Re-register against a FRESH endpoint (register_session
                    # re-resolves internally): in sandbox mode the claim may
                    # have been re-adopted under a new sandbox name.
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
                # Sandbox mode without a bootstrap payload: one re-resolve +
                # retry — the router 404/410 above means the sandbox was likely
                # re-adopted; the next loop iteration resolves the new name.
                continue
            return

    async def _stream_send(
        self,
        *,
        endpoint: RuntimeEndpoint,
        project_id: str,
        session_id: str,
        message: str,
        images: list[dict] | None = None,
        model: str | None,
        retry: dict | None = None,
        max_turns: int | None = None,
    ) -> AsyncIterator[AgentEvent]:
        # Fresh bridge JWT before every send: the token TTL (24h) is shorter
        # than a sandbox lifetime; a stale token makes runtime→API
        # callbacks 401. Re-bind is idempotent and throttled by mark/TTL.
        await ensure_recent_bind(self._session, project_id, endpoint=endpoint)
        url = f"{endpoint.base_url}/v1/sessions/{session_id}/send"
        body: dict = {"message": message}
        if images:
            body["images"] = images
        bridge_model = sanitize_runtime_model(model)
        if bridge_model:
            body["model"] = bridge_model
        if max_turns is not None and max_turns > 0:
            # SendRequestSchema требует положительное целое; при «без
            # ограничений» поле не отправляем — работает конфиг проекта
            body["max_turns"] = int(max_turns)
        if retry:
            # SendRetryPolicy (chat reconnect policy) — provider-error
            # reconnects with interval/attempt budget + fallback models.
            body.update(retry)

        try:
            yielded = False
            timeout = httpx.Timeout(
                connect=_SEND_CONNECT_TIMEOUT_SEC,
                read=_SEND_READ_TIMEOUT_SEC,
                write=_SEND_WRITE_TIMEOUT_SEC,
                pool=_SEND_CONNECT_TIMEOUT_SEC,
            )
            async with self._http_client(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    url,
                    json=body,
                    headers=_runtime_request_headers(endpoint.headers),
                ) as response:
                    if response.status_code in (404, 410):
                        # Router: the claim's sandbox is gone / re-adopted —
                        # drop the cached claim→sandbox mapping so the
                        # re-resolve on retry fetches the fresh sandbox name.
                        await invalidate_sandbox_name_cache(
                            sandbox_name=str(endpoint.headers.get("X-Sandbox-Id") or "") or None,
                        )
                    if response.status_code >= 400:
                        text = await response.aread()
                        body_text = text.decode("utf-8", errors="replace")
                        lowered = body_text.lower()
                        code = "BRIDGE_SEND_FAILED"
                        retryable = response.status_code >= 500
                        if response.status_code == 410:
                            # Router: sandbox gone / re-adopted under a new name.
                            code = "BRIDGE_SESSION_NOT_FOUND"
                        elif response.status_code == 409 and "hydrating" in lowered:
                            # Runtime is self-hydrating from the bind payload;
                            # recoverable — iter_send_events re-registers.
                            code = "BRIDGE_HYDRATING"
                            retryable = True
                        elif response.status_code == 404 and (
                            "session not found" in lowered or "sandbox" in lowered
                        ):
                            code = "BRIDGE_SESSION_NOT_FOUND"
                        yield AgentEvent.now(
                            AgentEventType.ERROR,
                            {
                                "code": code,
                                "message": body_text[:500],
                                "retryable": retryable,
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
        except httpx.TimeoutException as exc:
            # Read/connect/pool/write timeout — the runtime (or router) went
            # silent. Recoverable: iter_send_events re-registers and retries
            # once against a freshly resolved endpoint.
            logger.warning("openclaw send timeout session=%s: %s", session_id, exc)
            yield AgentEvent.now(
                AgentEventType.ERROR,
                {
                    "code": "BRIDGE_TIMEOUT",
                    "message": f"agent runtime timed out ({type(exc).__name__})",
                    "retryable": True,
                },
            )
        except Exception as exc:
            logger.warning("openclaw send proxy failed session=%s: %s", session_id, exc)
            yield AgentEvent.now(
                AgentEventType.ERROR,
                {"code": "BRIDGE_UNREACHABLE", "message": str(exc), "retryable": True},
            )

    async def _resolve_endpoint_for_project(self, project_id: str) -> RuntimeEndpoint | None:
        return await resolve_runtime_endpoint(
            self._session,
            project_id,
            k8s_client=self._k8s,
        )

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
        endpoint = await self._resolve_endpoint_for_project(project_id)
        if endpoint is None:
            return False
        bridge_decision = "allow" if decision == "approve" else "deny"
        url = f"{endpoint.base_url}/v1/approvals/{approval_id}"
        try:
            async with self._http_client(timeout=10.0) as client:
                response = await client.post(
                    url,
                    json={"decision": bridge_decision},
                    headers=_runtime_request_headers(endpoint.headers),
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
        endpoint = await self._resolve_endpoint_for_project(project_id)
        if endpoint is None:
            return None
        url = f"{endpoint.base_url}/v1/sessions/{session_id}/sidechains/{tool_use_id}/transcript"
        try:
            async with self._http_client(timeout=10.0) as client:
                response = await client.get(url, headers=_runtime_request_headers(endpoint.headers))
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
        endpoint = await self._resolve_endpoint_for_project(project_id)
        if endpoint is None:
            return None
        url = f"{endpoint.base_url}/v1/sessions/{source_session_id}/fork"
        try:
            async with self._http_client(timeout=10.0) as client:
                response = await client.post(
                    url,
                    json={"session_id": new_session_id},
                    headers=_runtime_request_headers(endpoint.headers),
                )
            if response.status_code >= 400:
                return None
            body = response.json()
            return body if isinstance(body, dict) else None
        except Exception as exc:
            logger.debug("fork session failed %s: %s", source_session_id, exc)
            return None
