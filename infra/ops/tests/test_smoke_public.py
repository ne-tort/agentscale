from __future__ import annotations

import httpx
import pytest

from prodavan_ops.smoke import smoke_public_https

_TS_SHELL = '<!doctype html><html><body><div id="root"></div><script src="/assets/x.js"></script></body></html>'
_INDEX_HTML = "<!doctype html><html><body><div id=\"root\"></div><script src=\"/assets/x.js\"></script></body></html>"


def _client(routes: dict[str, tuple[int, str, str]]):
    """routes: path -> (status, body, content-type)."""

    class FakeResp:
        def __init__(self, status: int, text: str, content_type: str) -> None:
            self.status_code = status
            self.text = text
            self.headers = {"content-type": content_type}

        def json(self):
            import json

            return json.loads(self.text)

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            self.headers = kwargs.get("headers") or {}

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args) -> None:  # noqa: ANN002
            return None

        def get(self, url: str, headers: dict | None = None) -> FakeResp:
            path = "/" + url.split("/", 3)[3] if url.count("/") >= 3 else "/"
            status, body, ctype = routes.get(path, (404, "", "text/plain"))
            return FakeResp(status, body, ctype)

    return FakeClient


def test_public_smoke_accepts_a_working_tls_router(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = {
        "/api/v1/auth/config": (200, '{"auth_mode":"oidc","features":{}}', "application/json"),
        "/": (200, _TS_SHELL, "text/html"),
    }
    monkeypatch.setattr(httpx, "Client", _client(routes))
    smoke_public_https(host="agentscale.dev", addr="127.0.0.1", attempts=1, sleep_sec=0)


def test_public_smoke_rejects_api_served_by_the_web_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact failure: `/api` falls through to nginx and returns the SPA.

    nginx answers a browser route with index.html, so a 200 with an HTML body is
    the fingerprint — not a 404, and not a 5xx, which is why a status-only check
    would pass.
    """
    routes = {
        "/api/v1/auth/config": (200, _INDEX_HTML, "text/html"),
        "/": (200, _TS_SHELL, "text/html"),
    }
    monkeypatch.setattr(httpx, "Client", _client(routes))
    with pytest.raises(RuntimeError, match="not reaching agentscale-api"):
        smoke_public_https(host="agentscale.dev", addr="127.0.0.1", attempts=1, sleep_sec=0)


def test_public_smoke_rejects_the_405_from_a_post_only_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = {
        "/api/v1/auth/config": (405, "<html>405 Not Allowed</html>", "text/html"),
        "/": (200, _TS_SHELL, "text/html"),
    }
    monkeypatch.setattr(httpx, "Client", _client(routes))
    with pytest.raises(RuntimeError, match=r"-> 405"):
        smoke_public_https(host="agentscale.dev", addr="127.0.0.1", attempts=1, sleep_sec=0)


def test_public_smoke_rejects_a_missing_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = {
        "/api/v1/auth/config": (200, '{"auth_mode":"oidc"}', "application/json"),
        "/": (200, "<html><body>flutter_bootstrap.js</body></html>", "text/html"),
    }
    monkeypatch.setattr(httpx, "Client", _client(routes))
    with pytest.raises(RuntimeError, match="not the web shell"):
        smoke_public_https(host="agentscale.dev", addr="127.0.0.1", attempts=1, sleep_sec=0)


def test_public_smoke_rejects_a_json_body_without_auth_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = {
        "/api/v1/auth/config": (200, '{"detail":"Not Found"}', "application/json"),
        "/": (200, _TS_SHELL, "text/html"),
    }
    monkeypatch.setattr(httpx, "Client", _client(routes))
    with pytest.raises(RuntimeError, match="returned"):
        smoke_public_https(host="agentscale.dev", addr="127.0.0.1", attempts=1, sleep_sec=0)
