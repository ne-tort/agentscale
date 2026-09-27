"""project_bind unit tests — POST /v1/project/bind contract (Wave 3).

The bind caller is strictly best-effort: never raises, tolerates 404 (old
runtime images), merges router selection headers, and skips the HTTP call
entirely when there is no live pod / project.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.agent import project_bind as pb
from prodavan.application.agent.runtime_transport import RuntimeEndpoint

EP = RuntimeEndpoint(
    base_url="http://sandbox-router-svc.agent-sandbox-system.svc:8080",
    headers={
        "X-Sandbox-Id": "sbx-abc123",
        "X-Sandbox-Namespace": "prodavan-sandboxes",
        "X-Sandbox-Port": "3921",
    },
)


class _Resp:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeHTTP:
    """Mimics httpx.AsyncClient as used by project_bind (async CM + post)."""

    def __init__(self, response: _Resp | None = None, *, raises: bool = False) -> None:
        self._response = response
        self._raises = raises
        self.posts: list[dict[str, Any]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> _FakeHTTP:
        return self

    async def __aenter__(self) -> _FakeHTTP:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def post(self, url: str, json: Any = None, headers: Any = None) -> _Resp:
        self.posts.append({"url": url, "json": json, "headers": headers})
        if self._raises:
            raise RuntimeError("boom")
        assert self._response is not None
        return self._response


def _session_with(project: Any = None, pod: Any = None) -> MagicMock:
    session = MagicMock()
    session.get = AsyncMock(return_value=project)
    exec_result = MagicMock()
    exec_result.scalar_one_or_none = MagicMock(return_value=pod)
    session.execute = AsyncMock(return_value=exec_result)
    return session


def _project() -> MagicMock:
    return MagicMock(id="prj_1", cabinet_id="cab_1", company_id="co_1", workspace_key="ws-1")


def _pod() -> MagicMock:
    return MagicMock(id="pod_1", workspace_key="ws-1")


def _patch_io(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pb, "_mint_bind_token", AsyncMock(return_value="jwt-token"))
    monkeypatch.setattr(pb, "_load_bind_env", AsyncMock(return_value={"OPENAI_BASE_URL": "https://x"}))
    monkeypatch.setattr(pb.settings, "pod_agent_runtime_token", "rt-token")
    monkeypatch.setattr(
        pb.settings,
        "pod_agent_runtime_api_base_url",
        "http://prodavan-api.prodavan.svc:8001/api/v1",
    )


@pytest.mark.asyncio
async def test_bind_posts_contract_body_with_router_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_io(monkeypatch)
    http = _FakeHTTP(_Resp(200))
    ok = await pb.bind_project_runtime(
        _session_with(project=_project(), pod=_pod()),
        "prj_1",
        endpoint=EP,
        http_client=lambda **kw: http,  # type: ignore[arg-type]
    )
    assert ok is True
    assert len(http.posts) == 1
    call = http.posts[0]
    assert call["url"] == f"{EP.base_url}/v1/project/bind"
    body = call["json"]
    assert body["project_id"] == "prj_1"
    assert body["pod_id"] == "pod_1"
    assert body["workspace_key"] == "ws-1"
    assert body["api_base_url"] == "http://prodavan-api.prodavan.svc:8001/api/v1"
    assert body["auth_token"] == "jwt-token"
    assert body["env"] == {"OPENAI_BASE_URL": "https://x"}
    headers = call["headers"]
    # Router selection headers ride on top of the runtime auth header.
    assert headers["Authorization"] == "Bearer rt-token"
    assert headers["X-Sandbox-Id"] == "sbx-abc123"
    assert headers["X-Sandbox-Namespace"] == "prodavan-sandboxes"
    assert headers["X-Sandbox-Port"] == "3921"


@pytest.mark.asyncio
async def test_bind_tolerates_404_old_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtimes without the route (transition-period images) answer 404."""
    _patch_io(monkeypatch)
    http = _FakeHTTP(_Resp(404, "not found"))
    ok = await pb.bind_project_runtime(
        _session_with(project=_project(), pod=_pod()),
        "prj_1",
        endpoint=EP,
        http_client=lambda **kw: http,  # type: ignore[arg-type]
    )
    assert ok is False


@pytest.mark.asyncio
async def test_bind_never_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_io(monkeypatch)
    http = _FakeHTTP(raises=True)
    ok = await pb.bind_project_runtime(
        _session_with(project=_project(), pod=_pod()),
        "prj_1",
        endpoint=EP,
        http_client=lambda **kw: http,  # type: ignore[arg-type]
    )
    assert ok is False


@pytest.mark.asyncio
async def test_bind_skips_http_when_no_live_pod(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_io(monkeypatch)
    http = _FakeHTTP(_Resp(200))
    ok = await pb.bind_project_runtime(
        _session_with(project=_project(), pod=None),
        "prj_1",
        endpoint=EP,
        http_client=lambda **kw: http,  # type: ignore[arg-type]
    )
    assert ok is False
    assert http.posts == []


@pytest.mark.asyncio
async def test_bind_skips_when_project_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_io(monkeypatch)
    http = _FakeHTTP(_Resp(200))
    ok = await pb.bind_project_runtime(
        _session_with(project=None, pod=None),
        "prj_missing",
        endpoint=EP,
        http_client=lambda **kw: http,  # type: ignore[arg-type]
    )
    assert ok is False
    assert http.posts == []


@pytest.mark.asyncio
async def test_bind_returns_false_without_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """No resolvable endpoint (not running / claim not adopted) → no HTTP call."""
    _patch_io(monkeypatch)
    monkeypatch.setattr(pb, "resolve_runtime_endpoint", AsyncMock(return_value=None))
    http = _FakeHTTP(_Resp(200))
    ok = await pb.bind_project_runtime(
        _session_with(project=_project(), pod=_pod()),
        "prj_1",
        endpoint=None,
        http_client=lambda **kw: http,  # type: ignore[arg-type]
    )
    assert ok is False
    assert http.posts == []
