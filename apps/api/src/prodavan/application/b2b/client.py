"""Minimal async client for the B2B-Center Integration API (read-only).

The Integration API is supplier-read-only: login with a regular account, then
`RecommendedList`, `TradesWithMeList` and `GetShortTrade`. We intentionally keep
this client small (no Redis, no metrics) — the importer runs inside the API
process/Celery worker and already has PostgreSQL/Redis infrastructure.

Rate limit observed on the real stand: 10 requests/minute; a 429 can impose a
~1 hour cooldown, so calls are serialized with a conservative interval.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from .dto import B2BCard, B2BTradeRef


class B2BError(RuntimeError):
    """Base integration error."""

    retryable = False


class B2BUnavailable(B2BError):
    retryable = True


class B2BUnauthorized(B2BError):
    pass


class B2BNotFound(B2BError):
    pass


class B2BCenterClient:
    def __init__(
        self,
        *,
        base_url: str,
        login: str,
        password: str,
        fmt: str = "json",
        lang: str = "rus",
        timeout: float = 30.0,
        min_interval: float = 6.0,
        max_retries: int = 3,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._fmt = fmt
        self._lang = lang
        self._login = login
        self._password = password
        self._timeout = max(1.0, timeout)
        self._min_interval = max(0.0, min_interval)
        self._max_retries = max(1, max_retries)
        self._http = http or httpx.AsyncClient()
        self._owns_http = http is None
        self._token = ""
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()
        self._call_lock = asyncio.Lock()
        self._last_call_at = 0.0

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def _throttle(self) -> None:
        async with self._call_lock:
            wait = self._min_interval - (time.monotonic() - self._last_call_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call_at = time.monotonic()

    async def _ensure_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at - 300:
            return self._token
        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expires_at - 300:
                return self._token
            await self._login_request()
            return self._token

    async def _login_request(self) -> None:
        payload = await self._request(
            "User.Login",
            {"login": self._login, "password": self._password},
            auth=False,
            http_method="POST",
            retry_auth=False,
        )
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise B2BUnauthorized("B2B-Center login returned no access_token")
        self._token = token
        self._token_expires_at = time.monotonic() + 30 * 60

    async def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        auth: bool = True,
        http_method: str = "GET",
        retry_auth: bool = True,
    ) -> dict[str, Any]:
        url = f"{self._base_url}/{self._fmt}/{method}/"
        query = {k: v for k, v in params.items() if v is not None}
        form: dict[str, Any] | None = None
        if http_method == "POST":
            form, query = query, {}
        if auth:
            query["access_token"] = await self._ensure_token()

        auth_retried = False
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            await self._throttle()
            try:
                response = await self._http.request(
                    http_method,
                    url,
                    params=query,
                    data=form,
                    timeout=self._timeout,
                    headers={"User-Agent": "agentscale-b2b/1.0 (read-only)"},
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(min(8.0, 0.8 * 2 ** (attempt - 1)))
                    continue
                raise B2BUnavailable(f"{method}: transport error: {exc}") from exc

            if response.status_code == 401 and auth and retry_auth and not auth_retried:
                auth_retried = True
                self._token = ""
                self._token_expires_at = 0.0
                await self._login_request()
                query["access_token"] = self._token
                continue
            if response.status_code in (401, 403):
                raise B2BUnauthorized(f"{method}: HTTP {response.status_code}")
            if response.status_code == 404:
                raise B2BNotFound(f"{method}: HTTP 404")
            if response.status_code == 429:
                if attempt < self._max_retries:
                    await asyncio.sleep(min(60.0, 5.0 * 3 ** (attempt - 1)))
                    continue
                raise B2BUnavailable(f"{method}: rate limited (HTTP 429)")
            if response.status_code >= 500:
                if attempt < self._max_retries:
                    await asyncio.sleep(min(15.0, 1.0 * 2 ** (attempt - 1)))
                    continue
                raise B2BUnavailable(f"{method}: HTTP {response.status_code}")
            if response.status_code >= 400:
                raise B2BError(f"{method}: HTTP {response.status_code}: {response.text[:200]}")
            try:
                payload = response.json()
            except ValueError as exc:
                if attempt < self._max_retries:
                    await asyncio.sleep(1.0)
                    continue
                raise B2BUnavailable(f"{method}: non-JSON response") from exc
            if not isinstance(payload, dict):
                raise B2BError(f"{method}: unexpected payload {type(payload).__name__}")
            error = _error_text(payload)
            if error:
                if attempt < self._max_retries and _looks_retryable(error):
                    await asyncio.sleep(min(8.0, 0.8 * 2 ** (attempt - 1)))
                    last_error = B2BError(error)
                    continue
                raise B2BError(f"{method}: {error}")
            return payload
        raise B2BUnavailable(f"{method}: retries exhausted: {last_error}")

    async def recommended_list(self) -> list[B2BTradeRef]:
        payload = await self._request("TradeProcedures.RecommendedList", {})
        return _trade_list(payload)

    async def trades_with_me_list(self) -> list[B2BTradeRef]:
        payload = await self._request("TradeProcedures.TradesWithMeList", {})
        return _trade_list(payload)

    async def get_card(self, uid: str) -> B2BCard:
        payload = await self._request(
            "TradeProcedures.GetShortTrade", {"id": uid, "lang": self._lang}
        )
        card = payload.get("short_trade_procedure")
        if not isinstance(card, dict) or not card:
            raise B2BNotFound(f"card {uid!r} not found")
        parsed = B2BCard.from_api(card)
        if not parsed.uid:
            parsed.uid = uid
        return parsed


def _trade_list(payload: dict[str, Any]) -> list[B2BTradeRef]:
    raw = payload.get("trade_list")
    if raw is None:
        raw = payload.get("result")
    items = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
    out: list[B2BTradeRef] = []
    for item in items:
        if isinstance(item, dict):
            ref = B2BTradeRef.from_api(item)
            if ref is not None:
                out.append(ref)
    return out


def _error_text(payload: dict[str, Any]) -> str | None:
    for key in ("error", "error_message", "errorMessage"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    code = payload.get("code")
    if isinstance(code, (int, str)) and str(code).strip() not in ("", "0", "200"):
        return f"api code {code}"
    return None


def _looks_retryable(text: str) -> bool:
    low = text.lower()
    return any(x in low for x in ("timeout", "tempor", "unavailable", "try again", "rate"))
