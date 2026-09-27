"""Sandbox-mode workspace + dehydrate adapters — router-addressed seams (Wave 3).

Covers only the sandbox-specific seams: endpoint resolution failure → 409,
archive streaming into the object-store upload, and the absence of a
kubectl-exec write fallback in sandbox mode. Shared HTTP verbs live in
HttpWorkspaceAdapterBase and are covered by test_workspace_http.py.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.agent.runtime_transport import RuntimeEndpoint
from prodavan.application.pod_service.adapters.agent_sandbox import dehydrate as dh
from prodavan.application.pod_service.adapters.agent_sandbox.dehydrate import (
    SandboxHttpDehydrateAdapter,
)
from prodavan.application.pod_service.adapters.agent_sandbox.workspace_http import (
    SandboxHttpWorkspaceAdapter,
)
from prodavan.application.pod_service.ports.dehydrate import DehydrateResult
from prodavan.config.settings import settings as _settings
from prodavan.domain.errors import AppError

REF = "sandbox-claim-ws-demo-01"
EP = RuntimeEndpoint(
    base_url="http://sandbox-router-svc.agent-sandbox-system.svc:8080",
    headers={
        "X-Sandbox-Id": "sbx-abc123",
        "X-Sandbox-Namespace": "prodavan-sandboxes",
        "X-Sandbox-Port": "3921",
    },
)


class _StreamResp:
    def __init__(self, status_code: int = 200, chunks: tuple[bytes, ...] = (), body: bytes = b"") -> None:
        self.status_code = status_code
        self._chunks = chunks
        self._body = body

    async def aread(self) -> bytes:
        return self._body

    async def aiter_bytes(self):  # type: ignore[no-untyped-def]
        for chunk in self._chunks:
            yield chunk


class _StreamCM:
    def __init__(self, resp: _StreamResp) -> None:
        self._resp = resp

    async def __aenter__(self) -> _StreamResp:
        return self._resp

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _FakeHTTP:
    """Mimics httpx.AsyncClient as used by SandboxHttpDehydrateAdapter."""

    def __init__(self, resp: _StreamResp) -> None:
        self._resp = resp
        self.streams: list[dict[str, Any]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> _FakeHTTP:
        return self

    async def __aenter__(self) -> _FakeHTTP:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    def stream(self, method: str, url: str, headers: Any = None) -> _StreamCM:
        self.streams.append({"method": method, "url": url, "headers": headers})
        return _StreamCM(self._resp)


def _patch_endpoint(monkeypatch: pytest.MonkeyPatch, endpoint: RuntimeEndpoint | None) -> None:
    monkeypatch.setattr(dh, "resolve_runtime_endpoint_for_ref", AsyncMock(return_value=endpoint))
    # runtime_auth_headers reads the canonical settings object directly.
    monkeypatch.setattr(_settings, "pod_agent_runtime_token", "rt-token")


# ---------------------------------------------------------------- dehydrate


@pytest.mark.asyncio
async def test_dehydrate_streams_archive_into_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_endpoint(monkeypatch, EP)
    uploaded: dict[str, Any] = {}

    def _fake_upload(*, workspace_key: str, tar_bytes: bytes) -> DehydrateResult:
        uploaded["workspace_key"] = workspace_key
        uploaded["tar_bytes"] = tar_bytes
        return DehydrateResult(uploaded=1, deleted=0)

    monkeypatch.setattr(dh, "upload_workspace_tar", _fake_upload)
    http = _FakeHTTP(_StreamResp(200, chunks=(b"ta", b"r.gz")))
    adapter = SandboxHttpDehydrateAdapter(http_client=lambda **kw: http)  # type: ignore[arg-type]
    result = await adapter.dehydrate(workspace_key="ws-1", runtime_ref=REF)
    assert result.uploaded == 1
    assert uploaded == {"workspace_key": "ws-1", "tar_bytes": b"tar.gz"}
    call = http.streams[0]
    assert call["url"] == f"{EP.base_url}/v1/workspace/archive"
    assert call["headers"]["Authorization"] == "Bearer rt-token"
    assert call["headers"]["X-Sandbox-Id"] == "sbx-abc123"


@pytest.mark.asyncio
async def test_dehydrate_409_when_endpoint_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_endpoint(monkeypatch, None)
    adapter = SandboxHttpDehydrateAdapter()
    with pytest.raises(AppError) as exc:
        await adapter.dehydrate(workspace_key="ws-1", runtime_ref=REF)
    assert exc.value.status == 409
    assert exc.value.code == "POD_NOT_RUNNING"


@pytest.mark.asyncio
async def test_dehydrate_502_on_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_endpoint(monkeypatch, EP)
    http = _FakeHTTP(_StreamResp(500, body=b"sandbox gone"))
    adapter = SandboxHttpDehydrateAdapter(http_client=lambda **kw: http)  # type: ignore[arg-type]
    with pytest.raises(AppError) as exc:
        await adapter.dehydrate(workspace_key="ws-1", runtime_ref=REF)
    assert exc.value.status == 502
    assert exc.value.code == "DEHYDRATE_FAILED"


@pytest.mark.asyncio
async def test_dehydrate_empty_archive_skips_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_endpoint(monkeypatch, EP)
    upload = MagicMock()
    monkeypatch.setattr(dh, "upload_workspace_tar", upload)
    http = _FakeHTTP(_StreamResp(200, chunks=()))
    adapter = SandboxHttpDehydrateAdapter(http_client=lambda **kw: http)  # type: ignore[arg-type]
    result = await adapter.dehydrate(workspace_key="ws-1", runtime_ref=REF)
    assert result == DehydrateResult(uploaded=0, deleted=0)
    upload.assert_not_called()


# ------------------------------------------------------------- workspace


@pytest.mark.asyncio
async def test_workspace_endpoint_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    from prodavan.application.pod_service.adapters.agent_sandbox import workspace_http as ws

    monkeypatch.setattr(ws, "resolve_runtime_endpoint_for_ref", AsyncMock(return_value=EP))
    adapter = SandboxHttpWorkspaceAdapter()
    assert await adapter._runtime_endpoint(REF) is EP


@pytest.mark.asyncio
async def test_workspace_409_when_endpoint_unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    from prodavan.application.pod_service.adapters.agent_sandbox import workspace_http as ws

    monkeypatch.setattr(ws, "resolve_runtime_endpoint_for_ref", AsyncMock(return_value=None))
    adapter = SandboxHttpWorkspaceAdapter()
    with pytest.raises(AppError) as exc:
        await adapter._runtime_endpoint(REF)
    assert exc.value.status == 409


@pytest.mark.asyncio
async def test_workspace_has_no_exec_write_fallback() -> None:
    """Sandbox mode must NOT fall back to kubectl exec (router-only access)."""
    adapter = SandboxHttpWorkspaceAdapter()
    with pytest.raises(AppError) as exc:
        await adapter._write_fallback(runtime_ref=REF, path="a.txt", data=b"x")
    assert exc.value.status == 502
    assert exc.value.code == "RUNTIME_FS_FAILED"
