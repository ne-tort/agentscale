"""Web-search proxy service — API surface the sandbox agent-runtime calls.

Architecture (CLAW-WEB): the agent pod must never reach SearxNG directly.
The agent's built-in `web.search` tool is configured (via env injected into the
sandbox) to use `provider=prodavan_api` and a URL pointing at this API's
`/projects/{project_id}/web-search` endpoint on the pod surface (:8001).

This service forwards the query to the real SearxNG backend (reachable only
from the API pod, not the sandbox namespace), enforces a per-pod rate limit,
and emits metrics. The pod sees a plain search provider and does not know it is
talking to a proxy.
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

# Result shape mirrors SearxNG JSON so the SDK `web.search` tool consumes it
# without a provider-specific adapter beyond the transport.
_SearchResultItem = dict[str, Any]
_SearchResult = dict[str, Any]

_DEFAULT_LIMIT = 5
_MAX_LIMIT = 20


class WebSearchService:
    """Proxy `web.search` queries from sandbox pods to the SearxNG backend."""

    def __init__(self, *, backend_url: str | None = None) -> None:
        self._backend_url = (backend_url or settings.web_search_backend_url or "").strip()

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
            # backend SearxNG URL is not set on the API side. Fail loudly so it
            # is noticed during deploy instead of returning an empty result.
            raise AppError(
                code="WEB_SEARCH_BACKEND_NOT_CONFIGURED",
                title="Web search unavailable",
                status=503,
                detail="WEB_SEARCH_BACKEND_URL is not set on the API pod",
            )

        resolved_limit = max(1, min(int(limit) if limit > 0 else _DEFAULT_LIMIT, _MAX_LIMIT))

        # Per-pod rate limit — keyed on pod_id so one pod cannot exhaust the
        # shared SearxNG backend. Window/limit are cluster-configured.
        await enforce_rate_limit(
            key=f"web_search:pod:{bridge.pod_id}",
            limit=settings.web_search_rate_limit,
            window_sec=settings.web_search_rate_window_sec,
            detail="web search rate limit exceeded for this pod",
        )

        url = self._backend_url
        # Allow a bare base URL (no path) — default to SearxNG `/search`.
        if "?" not in url and not url.endswith("/search"):
            url = url.rstrip("/") + "/search"
        params = {"q": query, "format": "json", "safesearch": "0"}
        headers = {"Accept": "application/json"}
        key = (settings.web_search_backend_api_key or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"

        started = time.monotonic()
        err: str | None = None
        status_code = 0
        try:
            async with httpx.AsyncClient(
                timeout=settings.web_search_timeout_sec,
                follow_redirects=True,
            ) as client:
                resp = await client.get(url, params=params, headers=headers)
            status_code = resp.status_code
            if resp.status_code >= 400:
                err = f"searxng HTTP {resp.status_code}"
                raise AppError(
                    code="WEB_SEARCH_BACKEND_ERROR",
                    title="Web search backend error",
                    status=502,
                    detail=f"searxng returned {resp.status_code}",
                )
            data = resp.json()
        except httpx.TimeoutException:
            err = "timeout"
            raise AppError(
                code="WEB_SEARCH_TIMEOUT",
                title="Web search timeout",
                status=504,
                detail="searxng did not respond in time",
            )
        except httpx.HTTPError as exc:
            err = f"fetch failed: {exc.__class__.__name__}"
            raise AppError(
                code="WEB_SEARCH_BACKEND_UNREACHABLE",
                title="Web search backend unreachable",
                status=502,
                detail=f"cannot reach searxng backend: {exc.__class__.__name__}",
            )
        finally:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            # Structured log line — the metrics aggregator can parse it. Keep the
            # query out of logs (PII / secrets); pod_id is already an authorized
            # identifier emitted elsewhere.
            logger.info(
                "web_search proxy pod=%s project=%s status=%s ms=%s err=%s",
                bridge.pod_id,
                bridge.project_id,
                status_code,
                elapsed_ms,
                err or "-",
            )

        results = (data or {}).get("results") or []
        items = [
            {
                "title": str(r.get("title") or ""),
                "url": str(r.get("url") or ""),
                "snippet": str(r.get("content") or ""),
            }
            for r in results[:resolved_limit]
        ]
        return {"results": items}
