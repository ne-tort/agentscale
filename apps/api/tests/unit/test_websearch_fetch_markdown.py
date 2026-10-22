"""`web.fetch` по умолчанию отдаёт markdown, а не сырой HTML.

Инцидент 2026-10-22: агент открыл страницу DNS (Qrator пройден), но «цена не
видна». Причина не в антиботе: `/fetch` отдаёт сырой DOM на 300-400 КБ, где
цены магазина размазаны по атрибутам (`data-price`) и script-состоянию —
модель их оттуда не достаёт. `/extract` прогоняет DOM через readability и
отдаёт ТЕКСТ, в котором цены уже есть (замер на живом DNS: 380 КБ HTML без
читаемых цен → 6.5 КБ markdown с 63 ценами вида «87 999 ₽»).

Эти тесты фиксируют контракт: по умолчанию — markdown через `/extract`;
`format=html` — сырой DOM через `/fetch`; если extract не дал текста (PDF,
бинарь, пустая страница) — честный fallback в HTML, а не пустота.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from prodavan.application.websearch.service import WebSearchService
from prodavan.domain.errors import AppError


class _Claims:
    pod_id = "pod-1"
    project_id = "proj-1"


def _claims() -> _Claims:
    return _Claims()


async def _no_limit(**_: Any) -> None:
    return None


class _Resp:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _Router:
    """Клиент, отвечающий по-разному на /extract и /fetch."""

    def __init__(self, extract: _Resp | Exception, fetch: _Resp | Exception) -> None:
        self._extract = extract
        self._fetch = fetch
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> "_Router":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def post(self, url: str, json: dict, headers: dict) -> _Resp:
        self.calls.append({"url": url, "body": json})
        target = self._extract if "/extract" in url else self._fetch
        if isinstance(target, Exception):
            raise target
        return target


@pytest.fixture()
def backend(monkeypatch):
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "web_search_backend_url", "http://searchnet.test:3950")
    monkeypatch.setattr(
        "prodavan.application.websearch.service.enforce_rate_limit", _no_limit
    )
    return "http://searchnet.test:3950"


def _install(monkeypatch, router: _Router) -> _Router:
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: router)
    return router


_MARKDOWN = (
    "Видеокарта Gigabyte RTX 5070\n\n"
    "87 999 ₽\n\nВ наличии в 221 магазине\n"
)


def _extract_ok(content: str = _MARKDOWN) -> _Resp:
    return _Resp(
        200,
        {
            "ok": True,
            "count": 1,
            "results": [
                {
                    "url": "https://www.dns-shop.ru/search/?q=rtx",
                    "final_url": "https://www.dns-shop.ru/search/?q=rtx",
                    "status": 200,
                    "block": "ok",
                    "content": content,
                    "warnings": [],
                }
            ],
        },
    )


def _fetch_ok(html: str = "<html><body>raw</body></html>") -> _Resp:
    return _Resp(
        200,
        {
            "url": "https://www.dns-shop.ru/search/?q=rtx",
            "final_url": "https://www.dns-shop.ru/search/?q=rtx",
            "status": 200,
            "block": "ok",
            "html": html,
        },
    )


async def test_default_returns_markdown_with_prices(monkeypatch, backend):
    """Главный сценарий: агент получает текст с ценой, а не 380 КБ HTML."""
    router = _install(monkeypatch, _Router(_extract_ok(), _fetch_ok()))
    out = await WebSearchService().fetch(
        bridge=_claims(), url="https://www.dns-shop.ru/search/?q=rtx"
    )

    assert router.calls[0]["url"] == f"{backend}/extract?detail=full"
    assert out["content_type"].startswith("text/markdown")
    assert "87 999 ₽" in out["content"]
    assert out["status"] == 200
    assert out["block"] == "ok"
    # HTML не запрашивался вовсе — незачем гонять 380 КБ.
    assert len(router.calls) == 1


async def test_extract_request_asks_for_full_large_text(monkeypatch, backend):
    """size=l + detail=full: compact отдаёт только превью, цены в него не влезают."""
    router = _install(monkeypatch, _Router(_extract_ok(), _fetch_ok()))
    await WebSearchService().fetch(bridge=_claims(), url="https://x/")
    body = router.calls[0]["body"]
    assert body["format"] == "markdown"
    assert body["size"] == "l"
    # mode=auto: searchnet сам эскалирует на браузер для anti-bot доменов.
    assert body["mode"] == "auto"


async def test_format_html_returns_raw_dom(monkeypatch, backend):
    html = "<html><body><span data-price='87999'>87 999 ₽</span></body></html>"
    router = _install(monkeypatch, _Router(_extract_ok(), _fetch_ok(html)))
    out = await WebSearchService().fetch(
        bridge=_claims(), url="https://x/", format="html"
    )
    assert router.calls[0]["url"] == f"{backend}/fetch"
    assert out["content_type"].startswith("text/html")
    assert out["content"] == html


async def test_falls_back_to_html_when_extract_empty(monkeypatch, backend):
    """PDF/бинарь/пустая страница: extract без текста — отдаём сырой HTML."""
    html = "%PDF-1.7 binary"
    router = _install(monkeypatch, _Router(_extract_ok(""), _fetch_ok(html)))
    out = await WebSearchService().fetch(bridge=_claims(), url="https://x/f.pdf")
    assert [c["url"].split("/")[-1] for c in router.calls][-1].startswith("fetch")
    assert out["content"] == html
    assert out["content_type"].startswith("text/html")


async def test_falls_back_when_extract_http_error(monkeypatch, backend):
    router = _install(monkeypatch, _Router(_Resp(502, {}), _fetch_ok("<p>ok</p>")))
    out = await WebSearchService().fetch(bridge=_claims(), url="https://x/")
    assert out["content"] == "<p>ok</p>"


async def test_falls_back_when_extract_times_out(monkeypatch, backend):
    router = _install(monkeypatch, _Router(httpx.ReadTimeout("t"), _fetch_ok("<p>ok</p>")))
    out = await WebSearchService().fetch(bridge=_claims(), url="https://x/")
    assert out["content"] == "<p>ok</p>"


async def test_markdown_truncated_to_max_bytes(monkeypatch, backend):
    router = _install(monkeypatch, _Router(_extract_ok("я" * 100), _fetch_ok()))
    out = await WebSearchService().fetch(
        bridge=_claims(), url="https://x/", max_bytes=50
    )
    assert out["truncated"] is True
    assert len(out["content"].encode("utf-8")) <= 50


async def test_markdown_block_surfaced(monkeypatch, backend):
    """Стена должна быть видна агенту, а не выглядеть пустой страницей."""
    resp = _extract_ok("Qrator challenge page")
    resp._payload["results"][0]["block"] = "challenge"
    _install(monkeypatch, _Router(resp, _fetch_ok()))
    out = await WebSearchService().fetch(bridge=_claims(), url="https://x/")
    assert out["block"] == "challenge"


async def test_html_request_still_requires_backend(monkeypatch, backend):
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "web_search_backend_url", "")
    with pytest.raises(AppError) as ei:
        await WebSearchService().fetch(bridge=_claims(), url="https://x/", format="html")
    assert ei.value.status == 503
