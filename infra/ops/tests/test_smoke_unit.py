from __future__ import annotations

import httpx
import pytest

from prodavan_ops.smoke import smoke

# What the TypeScript app actually serves at `/`: Vite's entry document.
_TS_SHELL = (
    '<!doctype html><html><head><title>Agentscale</title></head>'
    '<body><div id="root"></div>'
    '<script type="module" src="/assets/index-CpoHFNqm.js"></script>'
    "</body></html>"
)

# What the Flutter web image served. Both answer 200 at `/`, which is why the
# shell is checked for markers rather than for a status code.
_FLUTTER_SHELL = (
    "<!doctype html><html><head>"
    '<script src="flutter_bootstrap.js" async></script></head>'
    "<body></body></html>"
)


def _client_returning(html: str, *, code: int = 200, calls: list[str] | None = None):
    class FakeResp:
        def __init__(self) -> None:
            self.status_code = code
            self.text = html

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args) -> None:  # noqa: ANN002
            return None

        def get(self, url: str, headers: dict | None = None) -> FakeResp:
            if calls is not None:
                calls.append(url)
            return FakeResp()

    return FakeClient


def test_smoke_all_paths_200(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(httpx, "Client", _client_returning(_TS_SHELL, calls=calls))
    smoke(addr="10.0.0.1", port=8088, attempts=1, sleep_sec=0)
    joined = "\n".join(calls)
    assert "http://10.0.0.1:8088/health/live" in joined
    assert "http://10.0.0.1:8088/health/ready" in joined
    assert "http://10.0.0.1:8088/api/v1/auth/config" in joined
    assert "http://10.0.0.1:8088/" in joined


def test_smoke_fails_fast_on_connect_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args) -> None:  # noqa: ANN002
            return None

        def get(self, url: str, headers: dict | None = None) -> None:
            raise httpx.ConnectError("connection refused", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with pytest.raises(RuntimeError, match="unreachable"):
        smoke(addr="127.0.0.1", port=9, attempts=6, sleep_sec=0, connect_fail_limit=3)


def test_smoke_fails_on_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "Client", _client_returning(_TS_SHELL, code=503))
    with pytest.raises(RuntimeError, match="smoke failed"):
        smoke(addr="127.0.0.1", port=9, attempts=1, sleep_sec=0)


def test_smoke_rejects_the_flutter_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deploy that still serves the Flutter bundle must not pass smoke.

    Every path returns 200 in that case — the old image was healthy, it was just
    the wrong application — so the only signal is the entry document.
    """
    monkeypatch.setattr(httpx, "Client", _client_returning(_FLUTTER_SHELL))
    with pytest.raises(RuntimeError, match="not the TypeScript app"):
        smoke(addr="127.0.0.1", port=9, attempts=1, sleep_sec=0)


def test_smoke_rejects_a_shell_without_the_vite_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    html = '<!doctype html><html><body><div id="root"></div></body></html>'
    monkeypatch.setattr(httpx, "Client", _client_returning(html))
    with pytest.raises(RuntimeError, match="vite asset bundle"):
        smoke(addr="127.0.0.1", port=9, attempts=1, sleep_sec=0)
