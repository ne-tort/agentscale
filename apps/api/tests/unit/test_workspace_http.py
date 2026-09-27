"""Unit tests for HttpAgentRuntimeWorkspaceAdapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.pod_service.adapters.k8s.workspace_http import HttpAgentRuntimeWorkspaceAdapter
from prodavan.config.settings import settings as _real_settings
from prodavan.domain.errors import AppError
from prodavan.infrastructure.k8s.sandbox.client import PodSnapshot


def _running_pod(*, pod_ip: str = "10.42.0.77", name: str = "pod-wk-demo") -> PodSnapshot:
    return PodSnapshot(
        name=name,
        uid="u1",
        phase="Running",
        restarts=0,
        ready=True,
        labels={},
        pod_ip=pod_ip,
    )


def _mock_http_client(*, response: MagicMock | None = None) -> MagicMock:
    mock_http = MagicMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)
    if response is not None:
        mock_http.request = AsyncMock(return_value=response)
        mock_http.get = AsyncMock(return_value=response)
        mock_http.delete = AsyncMock(return_value=response)
    return mock_http


@pytest.fixture
def k8s_client() -> MagicMock:
    client = MagicMock()
    client.get_pod = AsyncMock(return_value=_running_pod())
    return client


@pytest.fixture
def adapter(k8s_client: MagicMock) -> HttpAgentRuntimeWorkspaceAdapter:
    return HttpAgentRuntimeWorkspaceAdapter(client=k8s_client)


@pytest.fixture(autouse=True)
def _pin_runtime_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # runtime_auth_headers() reads prodavan.config.settings directly.
    monkeypatch.setattr(_real_settings, "pod_agent_runtime_token", "")


@pytest.mark.asyncio
async def test_list_entries_parses_runtime_response(adapter: HttpAgentRuntimeWorkspaceAdapter) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "entries": [
            {"name": "README.md", "path": "README.md", "kind": "file", "size": 12},
            {"name": "src", "path": "src", "kind": "dir"},
        ],
    }
    mock_http = _mock_http_client(response=mock_response)

    with (
        patch("prodavan.application.pod_service.adapters.k8s.workspace_http.settings") as mock_settings,
        patch(
            "prodavan.application.pod_service.adapters.k8s.workspace_http.httpx.AsyncClient",
            return_value=mock_http,
        ),
    ):
        mock_settings.pod_agent_runtime_port = 3921
        entries = await adapter.list_entries(runtime_ref="pod-wk-demo", path=".")

    assert len(entries) == 2
    assert entries[0].name == "README.md"
    assert entries[1].kind == "dir"
    call = mock_http.request.await_args
    assert call.args[0] == "GET"
    assert "/v1/workspace/entries?path=" in call.args[1]


@pytest.mark.asyncio
async def test_read_bytes_returns_content(
    adapter: HttpAgentRuntimeWorkspaceAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"hello workspace"
    mock_http = _mock_http_client(response=mock_response)

    with (
        patch("prodavan.application.pod_service.adapters.k8s.workspace_http.settings") as mock_settings,
        patch(
            "prodavan.application.pod_service.adapters.k8s.workspace_http.httpx.AsyncClient",
            return_value=mock_http,
        ),
    ):
        mock_settings.pod_agent_runtime_port = 3921
        monkeypatch.setattr(_real_settings, "pod_agent_runtime_token", "rt")
        data = await adapter.read_bytes(runtime_ref="pod-wk-demo", path="README.md", max_bytes=4096)

    assert data == b"hello workspace"
    call = mock_http.get.await_args
    assert "max_bytes=4096" in call.args[0]
    assert call.kwargs["headers"]["Authorization"] == "Bearer rt"


@pytest.mark.asyncio
async def test_read_bytes_rejects_empty_path(adapter: HttpAgentRuntimeWorkspaceAdapter) -> None:
    with pytest.raises(AppError) as exc_info:
        await adapter.read_bytes(runtime_ref="pod-wk-demo", path="", max_bytes=1024)
    assert exc_info.value.code == "VALIDATION_ERROR"
    assert exc_info.value.status == 422


@pytest.mark.asyncio
async def test_stat_parses_entry(adapter: HttpAgentRuntimeWorkspaceAdapter) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "name": "AGENTS.md",
        "path": "AGENTS.md",
        "kind": "file",
        "size": 99,
    }
    mock_http = _mock_http_client(response=mock_response)

    with (
        patch("prodavan.application.pod_service.adapters.k8s.workspace_http.settings") as mock_settings,
        patch(
            "prodavan.application.pod_service.adapters.k8s.workspace_http.httpx.AsyncClient",
            return_value=mock_http,
        ),
    ):
        mock_settings.pod_agent_runtime_port = 3921
        entry = await adapter.stat(runtime_ref="pod-wk-demo", path="AGENTS.md")

    assert entry.name == "AGENTS.md"
    assert entry.kind == "file"
    assert entry.size == 99


@pytest.mark.asyncio
async def test_delete_sends_delete_request(adapter: HttpAgentRuntimeWorkspaceAdapter) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 204
    mock_http = _mock_http_client(response=mock_response)

    with (
        patch("prodavan.application.pod_service.adapters.k8s.workspace_http.settings") as mock_settings,
        patch(
            "prodavan.application.pod_service.adapters.k8s.workspace_http.httpx.AsyncClient",
            return_value=mock_http,
        ),
    ):
        mock_settings.pod_agent_runtime_port = 3921
        await adapter.delete(runtime_ref="pod-wk-demo", path="tmp/old.txt")

    call = mock_http.request.await_args
    assert call.args[0] == "DELETE"
    assert "tmp%2Fold.txt" in call.args[1] or "tmp/old.txt" in call.args[1]


@pytest.mark.asyncio
async def test_move_posts_src_dst(adapter: HttpAgentRuntimeWorkspaceAdapter) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {}
    mock_http = _mock_http_client(response=mock_response)

    with (
        patch("prodavan.application.pod_service.adapters.k8s.workspace_http.settings") as mock_settings,
        patch(
            "prodavan.application.pod_service.adapters.k8s.workspace_http.httpx.AsyncClient",
            return_value=mock_http,
        ),
    ):
        mock_settings.pod_agent_runtime_port = 3921
        await adapter.move(runtime_ref="pod-wk-demo", src="a.txt", dst="b.txt")

    call = mock_http.request.await_args
    assert call.args[0] == "POST"
    assert call.args[1].endswith("/v1/workspace/move")
    assert call.kwargs["json"] == {"src": "a.txt", "dst": "b.txt"}


@pytest.mark.asyncio
async def test_copy_requires_both_paths(adapter: HttpAgentRuntimeWorkspaceAdapter) -> None:
    with pytest.raises(AppError) as exc_info:
        await adapter.copy(runtime_ref="pod-wk-demo", src="", dst="b.txt")
    assert exc_info.value.code == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_pod_not_running_raises_conflict(k8s_client: MagicMock) -> None:
    k8s_client.get_pod = AsyncMock(
        return_value=PodSnapshot(
            name="pod-wk-demo",
            uid="u1",
            phase="Pending",
            restarts=0,
            ready=False,
            labels={},
            pod_ip=None,
        ),
    )
    adapter = HttpAgentRuntimeWorkspaceAdapter(client=k8s_client)

    with pytest.raises(AppError) as exc_info:
        await adapter.list_entries(runtime_ref="pod-wk-demo", path=".")
    assert exc_info.value.code == "POD_NOT_RUNNING"
    assert exc_info.value.status == 409


@pytest.mark.asyncio
async def test_runtime_http_error_maps_to_bad_gateway(adapter: HttpAgentRuntimeWorkspaceAdapter) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "internal error"
    mock_http = _mock_http_client(response=mock_response)

    with (
        patch("prodavan.application.pod_service.adapters.k8s.workspace_http.settings") as mock_settings,
        patch(
            "prodavan.application.pod_service.adapters.k8s.workspace_http.httpx.AsyncClient",
            return_value=mock_http,
        ),
    ):
        mock_settings.pod_agent_runtime_port = 3921
        with pytest.raises(AppError) as exc_info:
            await adapter.stat(runtime_ref="pod-wk-demo", path="missing.txt")

    assert exc_info.value.code == "RUNTIME_FS_FAILED"
    assert exc_info.value.status == 502
