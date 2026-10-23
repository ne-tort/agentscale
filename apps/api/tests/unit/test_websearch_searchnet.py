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


# ---------------------------------------------------------------- web.fetch

async def test_fetch_maps_rendered_dom(monkeypatch, backend):
    html = "<html><body><span class='price'>12 990 ₽</span></body></html>"
    resp = _FakeResp(
        200,
        {
            "url": "https://www.dns-shop.ru/product/x/",
            "final_url": "https://www.dns-shop.ru/product/x/",
            "status": 200,
            "block": "ok",
            "html": html,
            "warnings": [],
        },
    )
    client = _install(monkeypatch, resp)
    out = await WebSearchService().fetch(
        bridge=_claims(), url="https://www.dns-shop.ru/product/x/"
    )
    assert client.captured["url"] == f"{backend}/fetch"
    assert client.captured["body"] == {"url": "https://www.dns-shop.ru/product/x/"}
    assert out["url"] == "https://www.dns-shop.ru/product/x/"
    assert out["status"] == 200
    assert out["content_type"].startswith("text/html")
    assert out["content"] == html
    assert out["bytes"] == len(html.encode("utf-8"))
    assert out["truncated"] is False
    assert out["block"] == "ok"


async def test_fetch_truncates_to_max_bytes(monkeypatch, backend):
    html = "я" * 100  # 200 байт в utf-8
    resp = _FakeResp(200, {"url": "u", "status": 200, "block": "ok", "html": html})
    _install(monkeypatch, resp)
    out = await WebSearchService().fetch(bridge=_claims(), url="https://x/", max_bytes=50)
    assert out["truncated"] is True
    assert len(out["content"].encode("utf-8")) <= 50
    assert out["bytes"] == 200


async def test_fetch_surfaces_block_and_engine(monkeypatch, backend):
    resp = _FakeResp(
        200,
        {"url": "u", "status": 403, "block": "challenge", "html": "<html></html>"},
    )
    client = _install(monkeypatch, resp)
    out = await WebSearchService().fetch(
        bridge=_claims(), url="https://www.dns-shop.ru/", engine="camoufox", shop="dns-shop"
    )
    assert out["block"] == "challenge"
    assert client.captured["body"]["engine"] == "camoufox"
    assert client.captured["body"]["shop"] == "dns-shop"


async def test_fetch_requires_url_and_backend(monkeypatch, backend):
    with pytest.raises(AppError) as ei:
        await WebSearchService().fetch(bridge=_claims(), url="  ")
    assert ei.value.status == 400

    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "web_search_backend_url", "")
    with pytest.raises(AppError) as ei2:
        await WebSearchService().fetch(bridge=_claims(), url="https://x/")
    assert ei2.value.status == 503


async def test_fetch_http_error_and_timeout(monkeypatch, backend):
    _install(monkeypatch, _FakeResp(502, {}))
    with pytest.raises(AppError) as ei:
        await WebSearchService().fetch(bridge=_claims(), url="https://x/")
    assert ei.value.status == 502

    _install(monkeypatch, httpx.ReadTimeout("t"))
    with pytest.raises(AppError) as ei2:
        await WebSearchService().fetch(bridge=_claims(), url="https://x/")
    assert ei2.value.status == 504
