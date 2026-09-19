"""Probe an AI key via the platform probe pod (PROBE-P3).

The platform runs a single long-lived agent-runtime pod (`prodavan-probe-pod`)
owned by the platform (not a project sandbox). It exposes the same bridge API
as project pods: POST /v1/credentials/leases (push secret into in-memory store),
GET /v1/models?adapter=...&key_id=... (list models via the vendor SDK/HTTP
path that only exists inside agent-runtime), DELETE /v1/credentials/leases/:id
(revoke).

Why a pod for probing? Direct http_probe works for OpenAI-compatible /models
endpoints, but:
- SDK tokens (cursor_sdk) only expose list-models via the vendor SDK
  (`@cursor/sdk` `Cursor.models.list()`), not a public HTTP endpoint the API
  can call directly.
- Some providers (Cursor WorkOS gateway) do not expose list-models at all;
  the pod validates the token via a 1-token chat through the SDK adapter.

Flow (per probe):
1. Push a short-lived lease (ttl=pod_probe_lease_ttl_sec, default 120s) carrying
   the key secret into the probe pod in-memory credential store. The secret
   is never persisted (no env, no disk) and never reused after the probe.
2. GET /v1/models?adapter=<bridge adapter>&key_id=<key_id> — the pod resolves
   the lease, calls the vendor SDK/HTTP, returns the live model list.
3. Revoke the lease (best-effort, always runs) — the secret is dropped from
   the pod memory immediately. Even if revoke fails, the TTL expires it in
   ≤120s.

Reliability:
- never raises — returns ProbeResult (ok/error/unavailable)
- treats pod-unreachable as `unavailable` (infra, not auth)
- 401/403 from the vendor (via the pod) → `error` AUTH_INVALID (bad key)
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.agent.adapter_kinds import api_kind_to_bridge_adapter
from prodavan.application.ai_keys.probe.provider_resolver import ProviderResolver
from prodavan.config.settings import settings
from prodavan.domain.ai_keys import ApiKind, ProbeKind, ProbeResult, ProbeStatus, is_http_probe_kind
from prodavan.infrastructure.persistence.models.ai_keys import AiProviderKeyRow
from prodavan.infrastructure.secrets.store import SecretStore, get_secret_store

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _trim(msg: str) -> str:
    msg = (msg or "").strip()
    if len(msg) > 500:
        msg = msg[:500] + "…"
    return msg


class ProbePodService:
    """Probe an AI key against the platform probe pod (push lease → /v1/models → revoke)."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        secrets: SecretStore | None = None,
        http_client: type[httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        self._session = session
        self._secrets = secrets or get_secret_store()
        self._http_client = http_client
        self._resolver = ProviderResolver(session)

    async def probe_key(self, row: AiProviderKeyRow) -> ProbeResult:
        """Push a lease into the probe pod, GET /v1/models, revoke the lease."""
        if not settings.pod_probe_enabled:
            return self._unavailable("POD_PROBE_DISABLED", "pod probe is disabled")
        if row.api_kind == ApiKind.CLI_SUBSCRIPTION:
            return self._unavailable(
                "CLI_SUBSCRIPTION_NOT_PROBEABLE",
                "CLI subscription keys cannot be probed via HTTP/pod",
                provider=row.provider,
                api_kind=row.api_kind,
            )
        secret = await self._resolve_secret(row)
        if secret is None:
            return ProbeResult(
                status=ProbeStatus.ERROR,
                error_code="NO_SECRET",
                error_message="key has no secret stored",
                provider=row.provider,
                api_kind=row.api_kind,
            )
        adapter = api_kind_to_bridge_adapter(row.api_kind)
        base_url = settings.pod_probe_base_url.rstrip("/")
        lease_id = f"probe_{uuid.uuid4().hex[:16]}"
        # Resolve the HTTP provider endpoint (base_url / models_path / auth_scheme)
        # from the ai.http_providers catalog for HTTP api_kinds. For SDK kinds
        # (cursor_sdk) list-models is only available via the vendor SDK inside
        # the pod — no endpoint params are needed.
        endpoint = await self._resolver.resolve(
            api_kind=row.api_kind,
            provider=row.provider,
            secret=secret,
            catalog_entry_id=getattr(row, "catalog_entry_id", None),
        ) if is_http_probe_kind(row.api_kind) else None
        push_ok = await self._push_lease(base_url, lease_id, row.id, secret)
        if not push_ok:
            return self._unavailable(
                "PROBE_POD_UNREACHABLE",
                "probe pod did not accept the credential lease",
                provider=row.provider,
                api_kind=row.api_kind,
            )
        try:
            return await self._fetch_models(
                base_url,
                adapter=adapter,
                key_id=row.id,
                endpoint=endpoint,
                row=row,
            )
        finally:
            await self._revoke_lease(base_url, lease_id)

    async def _fetch_models(
        self,
        base_url: str,
        *,
        adapter: str,
        key_id: str,
        endpoint,
        row: AiProviderKeyRow,
    ) -> ProbeResult:
        url = f"{base_url}/v1/models"
        params: dict[str, str] = {"adapter": adapter, "key_id": key_id}
        if endpoint is not None:
            params["base_url"] = endpoint.base_url
            params["models_path"] = endpoint.models_path
            params["auth_scheme"] = endpoint.auth_scheme
        start = _now_ms()
        try:
            async with self._http_client(timeout=settings.pod_probe_timeout_sec) as client:
                response = await client.get(
                    url,
                    params={"adapter": adapter, "key_id": key_id},
                    headers=_runtime_request_headers(),
                )
        except httpx.TimeoutException as exc:
            return self._unavailable(
                "PROBE_TIMEOUT",
                f"timeout: {exc}",
                provider=row.provider,
                api_kind=row.api_kind,
                latency_ms=int(_now_ms() - start),
            )
        except httpx.HTTPError as exc:
            return self._unavailable(
                "PROBE_NETWORK",
                f"network: {exc}",
                provider=row.provider,
                api_kind=row.api_kind,
                latency_ms=int(_now_ms() - start),
            )
        latency = int(_now_ms() - start)
        return self._interpret_models_response(response, latency, row)

    def _interpret_models_response(
        self,
        response: httpx.Response,
        latency: int,
        row: AiProviderKeyRow,
    ) -> ProbeResult:
        status_code = response.status_code
        if 200 <= status_code < 300:
            try:
                body = response.json()
            except Exception:
                body = None
            models = _normalize_models(body)
            return ProbeResult(
                status=ProbeStatus.OK,
                kind=ProbeKind.MODELS,
                latency_ms=latency,
                models=models,
                default_model=models[0] if models else None,
                http_status=status_code,
                provider=row.provider,
                api_kind=row.api_kind,
            )
        body_text = _trim(response.text or "")
        if status_code in (401, 403):
            return ProbeResult(
                status=ProbeStatus.ERROR,
                kind=ProbeKind.MODELS,
                latency_ms=latency,
                http_status=status_code,
                error_code="AUTH_INVALID",
                error_message=body_text or "authentication failed (invalid key)",
                provider=row.provider,
                api_kind=row.api_kind,
            )
        if status_code == 429:
            return ProbeResult(
                status=ProbeStatus.OK,
                kind=ProbeKind.MODELS,
                latency_ms=latency,
                http_status=status_code,
                error_code="RATE_LIMITED",
                error_message=body_text or "rate limited (key valid but throttled)",
                provider=row.provider,
                api_kind=row.api_kind,
            )
        return ProbeResult(
            status=ProbeStatus.ERROR,
            kind=ProbeKind.MODELS,
            latency_ms=latency,
            http_status=status_code,
            error_code=f"HTTP_{status_code}",
            error_message=body_text or f"unexpected status {status_code}",
            provider=row.provider,
            api_kind=row.api_kind,
        )

    async def _push_lease(self, base_url: str, lease_id: str, key_id: str, secret: str) -> bool:
        url = f"{base_url}/v1/credentials/leases"
        body = {
            "lease_id": lease_id,
            "key_id": key_id,
            "secret": secret,
            "ttl_sec": max(60, min(int(settings.pod_probe_lease_ttl_sec), 86_400)),
        }
        try:
            async with self._http_client(timeout=5.0) as client:
                response = await client.post(url, json=body, headers=_runtime_request_headers())
            return response.status_code in (200, 201)
        except Exception as exc:
            logger.debug("probe pod push lease failed key=%s: %s", key_id, exc)
            return False

    async def _revoke_lease(self, base_url: str, lease_id: str) -> None:
        url = f"{base_url}/v1/credentials/leases/{lease_id}"
        try:
            async with self._http_client(timeout=5.0) as client:
                await client.delete(url, headers=_runtime_request_headers())
        except Exception as exc:
            logger.debug("probe pod revoke lease failed lease=%s: %s", lease_id, exc)

    async def _resolve_secret(self, row: AiProviderKeyRow) -> str | None:
        ref = (row.secret_ref or "").strip()
        if not ref:
            return None
        try:
            secret = self._secrets.get(ref)
        except Exception as exc:
            logger.warning("probe pod: secret fetch failed key=%s: %s", row.id, exc)
            return None
        if not (secret or "").strip():
            return None
        return secret

    def _unavailable(
        self,
        code: str,
        message: str,
        *,
        provider: str | None = None,
        api_kind: str | None = None,
        latency_ms: int | None = None,
    ) -> ProbeResult:
        return ProbeResult(
            status=ProbeStatus.UNAVAILABLE,
            error_code=code,
            error_message=_trim(message),
            provider=provider,
            api_kind=api_kind,
            latency_ms=latency_ms,
        )


def _normalize_models(body: object) -> list[str]:
    models: list[str] = []
    if isinstance(body, dict):
        raw = body.get("models") or body.get("data") or []
    elif isinstance(body, list):
        raw = body
    else:
        return models
    if not isinstance(raw, list):
        return models
    for item in raw:
        if isinstance(item, str) and item.strip():
            models.append(item.strip())
        elif isinstance(item, dict):
            mid = str(item.get("id") or item.get("name") or "").strip()
            if mid:
                models.append(mid)
    seen: set[str] = set()
    out: list[str] = []
    for m in models:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _runtime_request_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    token = settings.pod_agent_runtime_token.strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers
