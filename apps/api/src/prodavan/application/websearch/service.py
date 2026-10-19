"""Web-search proxy service — API surface the sandbox agent-runtime calls.

Architecture (CLAW-WEB v2): the agent pod must never reach searchnet directly.
The agent's built-in `web.search` tool is configured (via env injected into the
sandbox) to use `provider=prodavan_api` and a URL pointing at this API's
`/projects/{project_id}/web-search` endpoint on the pod surface (:8001).

This service forwards the query to the searchnet backend (reachable only from
the API pod, not the sandbox namespace), enforces a per-pod rate limit, and
emits metrics. The pod sees a plain search provider and does not know it is
talking to a proxy. SearXNG is gone (WAVE17): searchnet's native REST
`POST {base}/search` replaces it — `ok:false` is surfaced as an honest error,
`warnings`/`providers_failed` are logged, never silently dropped.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from prodavan.api.rate_limit import enforce_rate_limit
from prodavan.application.pod_identity.bridge import PodBridgeClaims
from prodavan.config.settings import settings
from prodavan.domain.errors import AppError

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 5
_MAX_LIMIT = 20


class WebSearchService:
    """Proxy `web.search` queries from sandbox pods to the searchnet backend."""

    def __init__(self, *, backend_url: str | None = None) -> None:
        self._backend_url = (backend_url or settings.web_search_backend_url or "").strip().rstrip("/")

    async def search(
        self,
        *,
        bridge: PodBridgeClaims,
        query: str,
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        query = (query or "").strip()
        if not query:
            raise AppError(
                code="WEB_SEARCH_QUERY_REQUIRED",
                title="Bad Request",
                status=400,
                detail="query is required",
            )
        if not self._backend_url:
            # Mis-deployment: the API proxy is wired into the sandbox but the
            # backend searchnet URL is not set on the API side. Fail loudly so it
            # is noticed during deploy instead of returning an empty result.
            raise AppError(
                code="WEB_SEARCH_BACKEND_NOT_CONFIGURED",
                title="Web search unavailable",
                status=503,
                detail="WEB_SEARCH_BACKEND_URL is not set on the API pod",
            )

        resolved_limit = max(1, min(int(limit) if limit > 0 else _DEFAULT_LIMIT, _MAX_LIMIT))

        # Per-pod rate limit — keyed on pod_id so one pod cannot exhaust the
        # shared searchnet backend (its SERP engines are anti-bot-sensitive).
        # Window/limit are cluster-configured.
        await enforce_rate_limit(
            key=f"web_search:pod:{bridge.pod_id}",
            limit=settings.web_search_rate_limit,
            window_sec=settings.web_search_rate_window_sec,
            detail="web search rate limit exceeded for this pod",
        )

        url = f"{self._backend_url}/search"
        # Native searchnet SearchQuery contract (docs/30-contracts/rest-api.md):
        # POST {base}/search {q, max_results} → {ok, results[], warnings[]}.
        body: dict[str, Any] = {"q": query, "max_results": resolved_limit}
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        key = (settings.web_search_backend_api_key or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"

        started = time.monotonic()
        err: str | None = None
        status_code = 0
        data: dict[str, Any] | None = None
        try:
            async with httpx.AsyncClient(
                timeout=settings.web_search_timeout_sec,
                follow_redirects=True,
            ) as client:
                resp = await client.post(url, json=body, headers=headers)
            status_code = resp.status_code
            if resp.status_code >= 400:
                err = f"searchnet HTTP {resp.status_code}"
                raise AppError(
                    code="WEB_SEARCH_BACKEND_ERROR",
                    title="Web search backend error",
                    status=502,
                    detail=f"searchnet returned {resp.status_code}",
                )
            data = resp.json()
        except httpx.TimeoutException:
            err = "timeout"
            raise AppError(
                code="WEB_SEARCH_TIMEOUT",
                title="Web search timeout",
                status=504,
                detail="searchnet did not respond in time",
            )
        except httpx.HTTPError as exc:
            err = f"fetch failed: {exc.__class__.__name__}"
            raise AppError(
                code="WEB_SEARCH_BACKEND_UNREACHABLE",
                title="Web search backend unreachable",
                status=502,
                detail=f"cannot reach searchnet backend: {exc.__class__.__name__}",
            )
        finally:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            # Structured log line — the metrics aggregator can parse it. Keep the
            # query out of logs (PII / secrets); pod_id is already an authorized
            # identifier emitted elsewhere. searchnet warnings (degraded engines
            # etc.) ride the same line so operators see the upstream health.
            warn = "-"; failed = "-"
            if data is not None:
                warn = ";".join(str(w) for w in (data.get("warnings") or [])[:3])
                failed = ";".join(str(p) for p in (data.get("providers_failed") or [])[:5])
            logger.info(
                "web_search proxy pod=%s project=%s status=%s ms=%s err=%s warn=%s failed=%s",
                bridge.pod_id,
                bridge.project_id,
                status_code,
                elapsed_ms,
                err or "-",
                warn,
                failed,
            )

        if data is None or data.get("ok") is False:
            # Honest-ok contract: ok:false = «сервис сказал, что искать нельзя»
            # (все движки сбойнули, admission full) — это НЕ пустая выдача.
            detail = str((data or {}).get("error") or "searchnet reported failure")
            raise AppError(
                code="WEB_SEARCH_BACKEND_ERROR",
                title="Web search backend error",
                status=502,
                detail=f"searchnet: {detail}",
            )

        results = (data or {}).get("results") or []
        items = [
            {
                "title": str(r.get("title") or ""),
                "url": str(r.get("canonical_url") or r.get("url") or ""),
                "snippet": str(r.get("snippet") or ""),
            }
            for r in results[:resolved_limit]
        ]
        return {"results": items}
