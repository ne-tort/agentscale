from __future__ import annotations

import httpx
import pytest

from prodavan_ops.smoke import smoke


def test_smoke_all_paths_200(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeResp:
        def __init__(self, code: int = 200) -> None:
            self.status_code = code

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *args) -> None:  # noqa: ANN002
            return None

        def get(self, url: str, headers: dict | None = None) -> FakeResp:
            calls.append(url)
            return FakeResp(200)

    monkeypatch.setattr(httpx, "Client", FakeClient)
    smoke(addr="10.0.0.1", port=8088, attempts=1, sleep_sec=0)
    joined = "\n".join(calls)
    assert "http://10.0.0.1:8088/health/live" in joined
    assert "http://10.0.0.1:8088/health/ready" in joined
    assert "http://10.0.0.1:8088/api/v1/auth/config" in joined
    assert "http://10.0.0.1:8088/" in joined


def test_smoke_fails_on_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResp:
        status_code = 503

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *args) -> None:  # noqa: ANN002
            return None

        def get(self, url: str, headers: dict | None = None) -> FakeResp:
            return FakeResp()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with pytest.raises(RuntimeError, match="smoke failed"):
        smoke(addr="127.0.0.1", port=9, attempts=1, sleep_sec=0)
