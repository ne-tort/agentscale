"""HTTP S4B client: delayed JSON/ZIP-JSON. P/N goes through sr=, never art=."""

from __future__ import annotations

import time
from typing import Any

import httpx

from prodavan.application.integrations.s4b_parse import (
    decode_body_bytes,
    parse_response,
    resolve_poll_url,
)
from prodavan.config.settings import settings


class HttpS4BGateway:
    def __init__(self) -> None:
        self._last_request_at = 0.0

    def ping(self, username: str, password: str) -> dict[str, Any]:
        raw = self._fetch(username, password, ["__ping__"])
        if raw.get("ok") is False:
            return raw
        return {"ok": True, "auth_ok": True}

    def search_by_part_numbers(
        self,
        username: str,
        password: str,
        part_numbers: list[str],
    ) -> dict[str, Any]:
        terms = [pn.strip() for pn in part_numbers if pn and pn.strip()]
        if not terms:
            return {"ok": False, "error_code": "empty_query", "error": "No part numbers"}
        raw = self._fetch(username, password, terms)
        if raw.get("ok") is False:
            return raw
        items, meta = parse_response(raw)
        return {"ok": True, "items": items, "meta": meta}

    def _cooldown(self) -> None:
        wait = settings.s4b_cooldown_seconds - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _fetch(self, username: str, password: str, terms: list[str]) -> dict[str, Any]:
        params = {
            "a": "10041",
            "at": "3",
            "usrLogin": username,
            "usrPassword": password,
            "delayed": "1",
            "sr": ";".join(terms) + ";",
        }
        parsed = self._get(settings.s4b_base_url, params)
        if parsed.get("error_code") == "rate_limited":
            parsed = self._get(settings.s4b_base_url, params)
        if parsed.get("ok") is False:
            return parsed
        if "results" in parsed:
            return parsed
        poll_url = resolve_poll_url(settings.s4b_base_url, parsed.get("url"))
        if not poll_url:
            if "list" in parsed:
                return {**parsed, "results": parsed.get("results") or []}
            for _ in range(settings.s4b_poll_attempts):
                time.sleep(settings.s4b_poll_delay_seconds)
                parsed = self._get(settings.s4b_base_url, params)
                if parsed.get("ok") is False:
                    return parsed
                if "results" in parsed:
                    return parsed
                poll_url = resolve_poll_url(settings.s4b_base_url, parsed.get("url"))
                if poll_url:
                    break
            if not poll_url:
                return {
                    "ok": False,
                    "error_code": "timeout",
                    "error": "S4B did not return a poll URL",
                }
        for _ in range(settings.s4b_poll_attempts):
            polled = self._get(poll_url, None)
            if polled.get("ok") is False:
                if polled.get("error_code") in {"parse", "upstream_error"}:
                    time.sleep(settings.s4b_poll_delay_seconds)
                    continue
                return polled
            if "results" in polled:
                return polled
            nxt = resolve_poll_url(settings.s4b_base_url, polled.get("url"))
            if nxt:
                poll_url = nxt
            time.sleep(settings.s4b_poll_delay_seconds)
        return {
            "ok": False,
            "error_code": "timeout",
            "error": "S4B poll did not return results",
        }

    def _get(self, url: str, params: dict[str, str] | None) -> dict[str, Any]:
        self._cooldown()
        try:
            response = httpx.get(url, params=params, timeout=settings.s4b_timeout_seconds)
        except httpx.TimeoutException:
            return {"ok": False, "error_code": "timeout", "error": "S4B HTTP timeout"}
        except httpx.HTTPError as exc:
            return {"ok": False, "error_code": "network", "error": str(exc)}
        return decode_body_bytes(response.content, response.headers.get("content-type", ""))
