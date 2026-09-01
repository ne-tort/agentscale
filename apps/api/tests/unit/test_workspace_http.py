"""Unit tests for HttpAgentRuntimeWorkspaceAdapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.pod_service.adapters.k8s.workspace_http import HttpAgentRuntimeWorkspaceAdapter
from prodavan.infrastructure.k8s.sandbox.client import PodSnapshot


@pytest.mark.asyncio
async def test_list_entries_parses_runtime_response() -> None:
    k8s = MagicMock()
    k8s.get_pod = AsyncMock(
        return_value=PodSnapshot(
            name="pod-wk-demo",
            uid="u1",
            phase="Running",
            restarts=0,
            ready=True,
            labels={},
            pod_ip="10.42.0.77",
        ),
    )
    adapter = HttpAgentRuntimeWorkspaceAdapter(client=k8s)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "entries": [
            {"name": "README.md", "path": "README.md", "kind": "file", "size": 12},
            {"name": "src", "path": "src", "kind": "dir"},
        ],
    }

    mock_http = MagicMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)
    mock_http.request = AsyncMock(return_value=mock_response)

    with (
        patch("prodavan.application.pod_service.adapters.k8s.workspace_http.settings") as mock_settings,
        patch("prodavan.application.pod_service.adapters.k8s.workspace_http.httpx.AsyncClient", return_value=mock_http),
    ):
        mock_settings.pod_agent_runtime_port = 3921
        mock_settings.pod_agent_runtime_token = ""
        entries = await adapter.list_entries(runtime_ref="pod-wk-demo", path=".")

    assert len(entries) == 2
    assert entries[0].name == "README.md"
    assert entries[1].kind == "dir"
