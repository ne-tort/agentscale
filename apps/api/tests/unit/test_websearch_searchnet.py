"""WAVE17 — websearch-прокси поверх searchnet (SearXNG удалён).

Проверяет WebSearchService: нативный REST-контракт POST /search, honest-ok
(ok:false → ошибка, НЕ пустая выдача), warnings/providers_failed в логе,
маппинг результатов в {title,url,snippet} для openclaw-sdk web.search.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from prodavan.application.pod_identity.bridge import PodBridgeClaims
from prodavan.application.websearch.service import WebSearchService
from prodavan.domain.errors import AppError


def _claims() -> PodBridgeClaims:
    return PodBridgeClaims(
        project_id="prj_w17",
        cabinet_id="cab_w17",
        company_id="com_w17",
        pod_id="pod_w17",
        gen=1,
        scopes=("pod.bridge",),
        jti="jti_w17",
        exp=9999999999,
    )


@pytest.fixture
def backend(monkeypatch):
    # Синглтон settings уже создан — env больше не читается; патчим атрибут.
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "web_search_backend_url", "http://searchnet.test.svc:3950")
    monkeypatch.setattr("prodavan.application.websearch.service.enforce_rate_limit", _no_limit)
    return "http://searchnet.test.svc:3950"


async def _no_limit(**_: Any) -> None:
    return None


class _FakeResp:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    def __init__(self, resp: _FakeResp | Exception) -> None:
        self._resp = resp

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def post(self, url: str, json: dict, headers: dict) -> _FakeResp:
        self.captured = {"url": url, "body": json, "headers": headers}
        if isinstance(self._resp, Exception):
            raise self._resp
        return self._resp


def _install(monkeypatch, resp: _FakeResp | Exception) -> _FakeClient:
    client = _FakeClient(resp)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client)
    return client


async def test_search_posts_native_contract(monkeypatch, backend):
    resp = _FakeResp(
        200,
        {
            "ok": True,
            "results": [
                {
                    "title": "Playwright",
                    "url": "https://playwright.dev/",
                    "canonical_url": "https://playwright.dev/",
                    "snippet": "end-to-end testing",
                },
                {"title": "t2", "url": "http://x/", "snippet": "s2"},
            ],
            "warnings": ["yandex cooldown"],
        },
    )
    client = _install(monkeypatch, resp)
    out = await WebSearchService().search(bridge=_claims(), query="playwright", limit=5)
    assert client.captured["url"] == f"{backend}/search"
    assert client.captured["body"] == {"q": "playwright", "max_results": 5}
    # canonical_url предпочтительнее url; snippet без content-фолбэка
    assert out["results"][0] == {
        "title": "Playwright",
        "url": "https://playwright.dev/",
        "snippet": "end-to-end testing",
    }
    assert out["results"][1]["url"] == "http://x/"


async def test_search_honest_ok_false_is_error_not_empty(monkeypatch, backend):
    resp = _FakeResp(
        200,
        {
            "ok": False,
            "code": "internal",
            "error": "metasearch: сбойнули все движки (7)",
            "retryable": False,
        },
    )
    _install(monkeypatch, resp)
    with pytest.raises(AppError) as ei:
        await WebSearchService().search(bridge=_claims(), query="q", limit=5)
    assert ei.value.status == 502
    assert "searchnet" in str(ei.value.detail)


async def test_search_http_error_mapped(monkeypatch, backend):
    _install(monkeypatch, _FakeResp(503, {}))
    with pytest.raises(AppError) as ei:
        await WebSearchService().search(bridge=_claims(), query="q", limit=5)
    assert ei.value.status == 502
    assert "503" in str(ei.value.detail)


async def test_search_timeout_and_unreachable(monkeypatch, backend):
    _install(monkeypatch, httpx.ReadTimeout("t"))
    with pytest.raises(AppError) as ei:
        await WebSearchService().search(bridge=_claims(), query="q", limit=5)
    assert ei.value.status == 504

    _install(monkeypatch, httpx.ConnectError("boom"))
    with pytest.raises(AppError) as ei:
        await WebSearchService().search(bridge=_claims(), query="q", limit=5)
    assert ei.value.status == 502


async def test_search_backend_not_configured(monkeypatch):
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "web_search_backend_url", "")
    with pytest.raises(AppError) as ei:
        await WebSearchService().search(bridge=_claims(), query="q", limit=5)
    assert ei.value.status == 503


async def test_search_limits_clamped(monkeypatch, backend):
    resp = _FakeResp(200, {"ok": True, "results": [{"title": "a", "url": "u", "snippet": "s"}] * 30})
    client = _install(monkeypatch, resp)
    await WebSearchService().search(bridge=_claims(), query="q", limit=99)
    assert client.captured["body"]["max_results"] == 20
