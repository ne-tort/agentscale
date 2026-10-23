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
# web.fetch: тот же дефолт/кап, что у SDK-тула (1MB / 5MB).
_MAX_FETCH_BYTES = 1 * 1024 * 1024
_MAX_FETCH_BYTES_CAP = 5 * 1024 * 1024
# Ретрай отказа рендера имеет смысл только для БЫСТРОГО отказа: медленный
# означает, что страница не влезает в бюджет, и второй заход выйдет за лимит
# тула. Порог с запасом ниже бюджета рендера (40с).
_RETRY_FAST_FAILURE_SEC = 25.0


def _retryable_failure(data: dict[str, Any] | None) -> bool:
    """searchnet сообщил о ВРЕМЕННОМ отказе (`failed[].retryable`)?

    Тяжёлые антибот-страницы (DNS через camoufox) иногда не укладываются в
    дедлайн рендера — это флап, а не постоянная ошибка. Отличаем его от
    «страница реально без текста» (PDF/бинарь/пустая), чтобы не ретраить
    заведомо бесполезное.
    """
    failed = (data or {}).get("failed") or []
    return any(isinstance(f, dict) and f.get("retryable") for f in failed)


class WebSearchService:
    """Proxy `web.search`/`web.fetch` from sandbox pods to the searchnet backend."""

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

    @staticmethod
    def _byte_limit(max_bytes: int | None) -> int:
        limit = max_bytes if isinstance(max_bytes, int) and max_bytes > 0 else _MAX_FETCH_BYTES
        return min(limit, _MAX_FETCH_BYTES_CAP)

    async def _fetch_markdown(
        self, *, url: str, engine: str = "", shop: str = ""
    ) -> dict[str, Any] | None:
        """searchnet `/extract?detail=full` → читаемый markdown страницы.

        Почему extract, а не fetch: магазины (DNS, Citilink, Ozon) дорисовывают
        цены/наличие/характеристики скриптами. `/fetch` отдаёт сырой DOM, где эти
        данные размазаны по атрибутам и script-состоянию — модель их оттуда не
        достаёт. `/extract` прогоняет DOM через readability/trafilatura и отдаёт
        ТЕКСТ, в котором цены уже есть (проверено на живом DNS: 380 КБ HTML без
        читаемых цен → 6.5 КБ markdown с 63 ценами вида «87 999 ₽»).

        `mode=auto` оставляем searchnet: он сам эскалирует на браузер, если
        http-слой упирается в стену (у доменов с anti-bot есть route).
        Возвращает None, если текста нет (PDF/бинарь/пустая страница) — тогда
        вызывающий падает в сырой HTML.
        """
        body: dict[str, Any] = {
            "url": url,
            "format": "markdown",
            # size=l: нужен полный текст карточки/выдачи, а не триаж-превью
            # (compact-режим отдаёт только content_preview, и цены в него не
            # попадают при длинной странице).
            "size": "l",
            "mode": "auto",
        }
        if engine.strip():
            body["engine"] = engine.strip()
        if shop.strip():
            body["shop"] = shop.strip()

        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        key = (settings.web_search_backend_api_key or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"

        endpoint = f"{self._backend_url}/extract?detail=full"
        # Один ретрай на ВРЕМЕННЫЙ отказ рендера. Замер на живом DNS: из пяти
        # подряд запросов два упали в «рендер не уложился в общий дедлайн» —
        # это флап тяжёлой страницы, а не постоянная ошибка (`retryable: true`
        # в ответе searchnet). Без ретрая агент в половине случаев получал
        # сырой HTML без цен и считал, что магазин не отдал цену.
        data: dict[str, Any] | None = None
        started = time.monotonic()
        for attempt in (1, 2):
            attempt_started = time.monotonic()
            err: str | None = None
            status_code = 0
            try:
                async with httpx.AsyncClient(
                    timeout=settings.web_fetch_timeout_sec,
                    follow_redirects=True,
                ) as client:
                    resp = await client.post(endpoint, json=body, headers=headers)
                status_code = resp.status_code
                if resp.status_code >= 400:
                    err = f"searchnet extract HTTP {resp.status_code}"
                    logger.warning("web_fetch markdown: %s (fallback to html)", err)
                    return None
                data = resp.json()
            except (httpx.TimeoutException, httpx.HTTPError) as exc:
                # extract не критичен: сырой HTML всё ещё можно отдать.
                logger.warning(
                    "web_fetch markdown failed (%s) — fallback to html",
                    exc.__class__.__name__,
                )
                return None
            finally:
                logger.info(
                    "web_fetch markdown attempt=%s status=%s ms=%s err=%s",
                    attempt,
                    status_code,
                    int((time.monotonic() - attempt_started) * 1000),
                    err or "-",
                )

            results = (data or {}).get("results") or []
            first = results[0] if results and isinstance(results[0], dict) else {}
            if str(first.get("content") or "").strip():
                break
            # Ретрай — только если попытка провалилась БЫСТРО. Медленный отказ
            # означает, что страница реально не влезает в бюджет рендера: второй
            # заход съест ещё столько же и выйдет за общий лимит тула (агент
            # получит обрыв вместо ответа). В этом случае лучше сразу отдать
            # сырой HTML — в нём цены тоже есть.
            elapsed_attempt = time.monotonic() - attempt_started
            if (
                attempt == 1
                and _retryable_failure(data)
                and elapsed_attempt < _RETRY_FAST_FAILURE_SEC
            ):
                logger.warning(
                    "web_fetch markdown: быстрый отказ рендера (%.1fс) — ретрай",
                    elapsed_attempt,
                )
                continue
            if _retryable_failure(data):
                logger.warning(
                    "web_fetch markdown: отказ рендера после %.1fс — без ретрая "
                    "(второй заход вышел бы за бюджет), отдаём HTML",
                    elapsed_attempt,
                )
            break
        logger.info(
            "web_fetch markdown total_ms=%s", int((time.monotonic() - started) * 1000)
        )

        results = (data or {}).get("results") or []
        if not results:
            return None
        first = results[0] if isinstance(results[0], dict) else {}
        content = str(first.get("content") or "")
        if not content.strip():
            return None
        return {
            "url": first.get("final_url") or first.get("url") or url,
            # `/extract` не отдаёт HTTP-статус страницы (он про извлечение, а не
            # про загрузку). Подставляем 200, когда извлечение удалось: иначе
            # агент видит status=0 и считает страницу битой, хотя текст с ценами
            # у него в руках. Если извлечение не удалось — 0 честно сигнализирует
            # «статуса нет».
            "status": int(first.get("status") or (200 if first.get("ok") else 0)),
            "block": first.get("block") or "ok",
            "content": content,
            "warnings": first.get("warnings") or [],
            "error": first.get("error"),
        }

    async def fetch(
        self,
        *,
        bridge: PodBridgeClaims,
        url: str,
        max_bytes: int | None = None,
        engine: str = "",
        shop: str = "",
        format: str = "markdown",
    ) -> dict[str, Any]:
        """CLAW-WEB — `web.fetch` proxy: agentscale API → searchnet.

        The agent's built-in `web.fetch` used to do a raw `fetch(url)` from the
        sandbox pod, which hit anti-bot walls (Qrator on DNS Shop etc.). Routing
        it through searchnet means the page is rendered by a real browser engine
        (chromium by default, camoufox escalation for Qrator/Cloudflare) with the
        shop profile/cookies, and the agent gets the rendered DOM.

        `format` — что отдать агенту:
          * ``markdown`` (дефолт) — searchnet ``/extract``: читаемый текст, в
            котором данные, дорисованные JS (цены/наличие/характеристики
            магазина), уже присутствуют. Это и есть рабочий режим: сырой HTML
            магазина — 300-400 КБ между тегами и script-состоянием, и модель на
            практике цену в нём не находит (инцидент 2026-10-22: агент открыл
            DNS, но «цена не видна»).
          * ``html`` — searchnet ``/fetch``: сырой DOM, когда нужен конкретный
            атрибут/разметка.

        Returns the same shape as the SDK's raw fetch (`url/status/content_type/
        bytes/truncated/content`) plus honest `block`/`warnings`/`error` so a
        wall is surfaced instead of a silently empty page.
        """
        url = (url or "").strip()
        if not url:
            raise AppError(
                code="WEB_FETCH_URL_REQUIRED",
                title="Bad Request",
                status=400,
                detail="url is required",
            )
        if not self._backend_url:
            raise AppError(
                code="WEB_SEARCH_BACKEND_NOT_CONFIGURED",
                title="Web search unavailable",
                status=503,
                detail="WEB_SEARCH_BACKEND_URL is not set on the API pod",
            )

        # Same per-pod web budget as web.search: one pod cannot burst searchnet
        # (its SERP/fetch engines are anti-bot-sensitive and browser-heavy).
        await enforce_rate_limit(
            key=f"web_search:pod:{bridge.pod_id}",
            limit=settings.web_search_rate_limit,
            window_sec=settings.web_search_rate_window_sec,
            detail="web rate limit exceeded for this pod",
        )

        want_markdown = (format or "").strip().lower() != "html"
        if want_markdown:
            payload = await self._fetch_markdown(url=url, engine=engine, shop=shop)
            if payload is not None:
                text = str(payload.get("content") or "")
                raw = text.encode("utf-8")
                limit = self._byte_limit(max_bytes)
                return {
                    "url": str(payload.get("url") or url),
                    "status": int(payload.get("status") or 0),
                    "content_type": "text/markdown; charset=utf-8",
                    "bytes": len(raw),
                    "truncated": len(raw) > limit,
                    "content": raw[:limit].decode("utf-8", "replace"),
                    "block": str(payload.get("block") or "ok"),
                    "warnings": [str(w) for w in (payload.get("warnings") or [])],
                    "error": payload.get("error"),
                }
            # extract не дал текста (PDF/бинарь/страница без основного контента) —
            # падаем в сырой HTML, чтобы агент не получил пустоту.

        body: dict[str, Any] = {"url": url}
        if engine.strip():
            body["engine"] = engine.strip()
        if shop.strip():
            body["shop"] = shop.strip()

        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        key = (settings.web_search_backend_api_key or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"

        endpoint = f"{self._backend_url}/fetch"
        started = time.monotonic()
        err: str | None = None
        status_code = 0
        data: dict[str, Any] | None = None
        try:
            async with httpx.AsyncClient(
                timeout=settings.web_fetch_timeout_sec,
                follow_redirects=True,
            ) as client:
                resp = await client.post(endpoint, json=body, headers=headers)
            status_code = resp.status_code
            if resp.status_code >= 400:
                err = f"searchnet HTTP {resp.status_code}"
                raise AppError(
                    code="WEB_FETCH_BACKEND_ERROR",
                    title="Web fetch backend error",
                    status=502,
                    detail=f"searchnet returned {resp.status_code}",
                )
            data = resp.json()
        except httpx.TimeoutException:
            err = "timeout"
            raise AppError(
                code="WEB_FETCH_TIMEOUT",
                title="Web fetch timeout",
                status=504,
                detail="searchnet did not respond in time",
            )
        except httpx.HTTPError as exc:
            err = f"fetch failed: {exc.__class__.__name__}"
            raise AppError(
                code="WEB_FETCH_BACKEND_UNREACHABLE",
                title="Web fetch backend unreachable",
                status=502,
                detail=f"cannot reach searchnet backend: {exc.__class__.__name__}",
            )
        finally:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "web_fetch proxy pod=%s project=%s status=%s ms=%s err=%s",
                bridge.pod_id,
                bridge.project_id,
                status_code,
                elapsed_ms,
                err or "-",
            )

        payload = data or {}
        html = str(payload.get("html") or "")
        raw = html.encode("utf-8")
        limit = max_bytes if isinstance(max_bytes, int) and max_bytes > 0 else _MAX_FETCH_BYTES
        limit = min(limit, _MAX_FETCH_BYTES_CAP)
        return {
            "url": str(payload.get("final_url") or payload.get("url") or url),
            "status": int(payload.get("status") or 0),
            "content_type": "text/html; charset=utf-8",
            "bytes": len(raw),
            "truncated": len(raw) > limit,
            "content": raw[:limit].decode("utf-8", "replace"),
            "block": str(payload.get("block") or "ok"),
            "warnings": [str(w) for w in (payload.get("warnings") or [])],
            "error": payload.get("error"),
        }
