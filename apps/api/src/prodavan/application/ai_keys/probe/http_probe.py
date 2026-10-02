"""HTTP probe client for AI key verification (PROBE-P1).

Two probe kinds:
1. GET /models — preferred (free, no tokens spent). Returns the list of
   models the key can access.
2. POST /chat/completions with 1 token — fallback when /models is not
   exposed by the provider (e.g. Anthropic Messages API).

Robustness rules:
- never raises — failures returned as ProbeResult(status=error|unavailable)
- measures latency (wall clock, ms) for the actual HTTP roundtrip
- handles timeouts / connection errors as `unavailable` (infra, not auth)
- maps 401/403 → auth error (key invalid), 429 → rate-limited (key valid but throttled)
- sanitizes error messages (no raw secret leaked; providers never echo it, but we trim)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from prodavan.domain.ai_keys.probe import ProbeKind, ProbeResult, ProbeStatus

logger = logging.getLogger(__name__)

# Probe timeouts: short enough to be "a quick check", long enough for cold TLS.
_PROBE_TIMEOUT_SEC = 15.0
_MAX_ERROR_LEN = 500


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _trim(msg: str) -> str:
    msg = (msg or "").strip()
    if len(msg) > _MAX_ERROR_LEN:
        msg = msg[:_MAX_ERROR_LEN] + "…"
    return msg


def _join_url(base_url: str, path: str) -> str:
    """Join base_url + path without duplicating a shared `/v1` prefix segment.

    Catalog seeds (e.g. ollama) carry base_url ending in `/v1` and models_path
    `/v1/models` — concatenating them yields `/v1/v1/models` (404, 0 models).
    When base_url already ends with the path's leading segment (e.g. `/v1`),
    drop it from the path before joining.
    """
    base = (base_url or "").rstrip("/")
    # Bare host (user-entered "cheapai.lol/v1" without a scheme) is not a
    # fetchable URL — default to https.
    if base and "://" not in base:
        base = f"https://{base}"
    p = path or ""
    if not p.startswith("/"):
        p = "/" + p
    # Last path segment of base (e.g. "/v1" for "https://x/v1"); skip the
    # scheme "//" pseudo-segment so "https://api.openai.com" does not match.
    seg = ""
    after_scheme = base.split("://", 1)[-1]
    slash = after_scheme.rfind("/")
    if slash >= 1:
        seg = after_scheme[slash:]
    if seg and p.startswith(seg + "/"):
        return base + p[len(seg):]
    if seg and p == seg:
        return base
    return base + p


def _auth_headers(endpoint, secret: str) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "prodavan-ai-key-probe/1.0",
    }
    scheme = endpoint.auth_scheme
    if scheme == "bearer":
        headers["Authorization"] = f"Bearer {secret}"
    elif scheme == "x-api-key":
        headers["x-api-key"] = secret
        # Anthropic also requires the anthropic-version header.
        headers["anthropic-version"] = "2023-06-01"
    # "none" → no auth (local ollama etc.)
    return headers


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
    # Dedup, preserve order.
    seen: set[str] = set()
    out: list[str] = []
    for m in models:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


async def _probe_models(endpoint, client: httpx.AsyncClient, secret: str) -> ProbeResult:
    url = _join_url(endpoint.base_url, endpoint.models_path)
    headers = _auth_headers(endpoint, secret)
    start = _now_ms()
    try:
        response = await client.get(url, headers=headers)
    except httpx.TimeoutException as exc:
        return ProbeResult(
            status=ProbeStatus.UNAVAILABLE,
            kind=ProbeKind.MODELS,
            latency_ms=int(_now_ms() - start),
            error_code="PROBE_TIMEOUT",
            error_message=_trim(f"timeout: {exc}"),
            provider=endpoint.agent_provider,
            api_kind=endpoint.api_kind,
        )
    except httpx.HTTPError as exc:
        return ProbeResult(
            status=ProbeStatus.UNAVAILABLE,
            kind=ProbeKind.MODELS,
            latency_ms=int(_now_ms() - start),
            error_code="PROBE_NETWORK",
            error_message=_trim(f"network: {exc}"),
            provider=endpoint.agent_provider,
            api_kind=endpoint.api_kind,
        )
    latency = int(_now_ms() - start)
    return _interpret_models_response(endpoint, response, latency)


def _interpret_models_response(endpoint, response: httpx.Response, latency: int) -> ProbeResult:
    status_code = response.status_code
    # 2xx — success
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
            provider=endpoint.agent_provider,
            api_kind=endpoint.api_kind,
        )
    return _classify_error(endpoint, response, latency, ProbeKind.MODELS)


def _classify_error(
    endpoint,
    response: httpx.Response,
    latency: int,
    kind: ProbeKind,
) -> ProbeResult:
    status_code = response.status_code
    body_text = ""
    try:
        body_text = response.text or ""
    except Exception:
        body_text = ""
    body_text = _trim(body_text)

    # 401/403 — auth invalid (the key is bad). Everything else is treated as a
    # transient/provider issue, but we still surface a meaningful code.
    if status_code in (401, 403):
        return ProbeResult(
            status=ProbeStatus.ERROR,
            kind=kind,
            latency_ms=latency,
            http_status=status_code,
            error_code="AUTH_INVALID",
            error_message=body_text or "authentication failed (invalid key)",
            provider=endpoint.agent_provider,
            api_kind=endpoint.api_kind,
        )
    if status_code == 429:
        # Rate-limited — the key itself is valid, but the provider throttled us.
        return ProbeResult(
            status=ProbeStatus.OK,  # auth works; just throttled
            kind=kind,
            latency_ms=latency,
            http_status=status_code,
            error_code="RATE_LIMITED",
            error_message=body_text or "rate limited (key valid but throttled)",
            provider=endpoint.agent_provider,
            api_kind=endpoint.api_kind,
        )
    return ProbeResult(
        status=ProbeStatus.ERROR,
        kind=kind,
        latency_ms=latency,
        http_status=status_code,
        error_code=f"HTTP_{status_code}",
        error_message=body_text or f"unexpected status {status_code}",
        provider=endpoint.agent_provider,
        api_kind=endpoint.api_kind,
    )


async def _probe_chat(
    endpoint,
    client: httpx.AsyncClient,
    secret: str,
    *,
    model: str | None = None,
) -> ProbeResult:
    """Minimal chat completion (1 token).

    Used as fallback when /models is not exposed by the provider, or to
    verify a *specific* model works with this key (probe_model).
    """
    url = _join_url(endpoint.base_url, endpoint.chat_completions_path)
    headers = _auth_headers(endpoint, secret)
    headers["Content-Type"] = "application/json"
    # Minimal payload — ask for 1 token. Works on OpenAI-compatible + Anthropic.
    payload: dict = {
        "model": model or "gpt-5.1",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }
    # Anthropic Messages API uses a slightly different shape.
    if endpoint.auth_scheme == "x-api-key":
        payload = {
            "model": model or "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
    start = _now_ms()
    try:
        response = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        return ProbeResult(
            status=ProbeStatus.UNAVAILABLE,
            kind=ProbeKind.CHAT,
            latency_ms=int(_now_ms() - start),
            error_code="PROBE_TIMEOUT",
            error_message=_trim(f"timeout: {exc}"),
            provider=endpoint.agent_provider,
            api_kind=endpoint.api_kind,
        )
    except httpx.HTTPError as exc:
        return ProbeResult(
            status=ProbeStatus.UNAVAILABLE,
            kind=ProbeKind.CHAT,
            latency_ms=int(_now_ms() - start),
            error_code="PROBE_NETWORK",
            error_message=_trim(f"network: {exc}"),
            provider=endpoint.agent_provider,
            api_kind=endpoint.api_kind,
        )
    latency = int(_now_ms() - start)
    status_code = response.status_code
    if 200 <= status_code < 300:
        return ProbeResult(
            status=ProbeStatus.OK,
            kind=ProbeKind.CHAT,
            latency_ms=latency,
            http_status=status_code,
            provider=endpoint.agent_provider,
            api_kind=endpoint.api_kind,
        )
    # 404 / 400 with "model not found" — auth worked, model name wrong.
    # Treat as OK-auth (key valid) but note the error.
    body_text = _trim(response.text or "")
    if status_code in (400, 404) and any(s in body_text.lower() for s in ("model", "not found", "invalid")):
        return ProbeResult(
            status=ProbeStatus.ERROR,
            kind=ProbeKind.CHAT,
            latency_ms=latency,
            http_status=status_code,
            error_code="MODEL_NOT_FOUND",
            error_message=body_text or "model not found (key valid)",
            provider=endpoint.agent_provider,
            api_kind=endpoint.api_kind,
        )
    return _classify_error(endpoint, response, latency, ProbeKind.CHAT)


class HttpProbeClient:
    """Performs HTTP probes against a resolved ProviderEndpoint."""

    def __init__(self, *, timeout: float = _PROBE_TIMEOUT_SEC) -> None:
        self._timeout = timeout

    async def probe(self, endpoint, secret: str) -> ProbeResult:
        """Probe a key: GET /models first, fall back to chat completion.

        If the endpoint does not support a models list (Cursor WorkOS gateway),
        skip /models and validate the token via a 1-token chat completion.
        """
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
        ) as client:
            if getattr(endpoint, "supports_models_list", True):
                result = await _probe_models(endpoint, client, secret)
                if result.status == ProbeStatus.OK:
                    return result
                # If /models failed with a hard auth error (401/403), don't retry
                # with chat — the key is invalid, no point spending tokens.
                if result.error_code == "AUTH_INVALID":
                    return result
                # If /models endpoint simply not implemented (404/405), try chat.
                if result.http_status in (404, 405) or result.error_code in ("HTTP_404", "HTTP_405"):
                    return await _probe_chat(endpoint, client, secret)
                return result
            # No models-list support — validate the token via a chat probe.
            return await _probe_chat(endpoint, client, secret)

    async def probe_model(self, endpoint, secret: str, *, model: str) -> ProbeResult:
        """Verify a specific model works with this key (1-token chat)."""
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
        ) as client:
            return await _probe_chat(endpoint, client, secret, model=model)


async def probe_http(
    endpoint,
    secret: str,
    *,
    timeout: float = _PROBE_TIMEOUT_SEC,
) -> ProbeResult:
    """Convenience function — create a client and probe."""
    return await HttpProbeClient(timeout=timeout).probe(endpoint, secret)


async def probe_http_model(
    endpoint,
    secret: str,
    model: str,
    *,
    timeout: float = _PROBE_TIMEOUT_SEC,
) -> ProbeResult:
    """Convenience function — probe a specific model."""
    return await HttpProbeClient(timeout=timeout).probe_model(endpoint, secret, model=model)
