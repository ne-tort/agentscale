"""Unit tests — K8sExecWorkspaceAdapter with mocked pod exec."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from prodavan.application.pod_service.adapters.k8s.workspace_exec import K8sExecWorkspaceAdapter
from prodavan.infrastructure.k8s.sandbox.client import PodSnapshot
from prodavan.infrastructure.k8s.sandbox.exec import ExecResult


def _running_pod(name: str = "pod-demo") -> PodSnapshot:
    return PodSnapshot(
        name=name,
        uid="uid-1",
        phase="Running",
        restarts=0,
        ready=True,
        labels={},
    )


@pytest.mark.asyncio
async def test_list_entries_parses_workspace_fs_json(monkeypatch) -> None:
    client = AsyncMock()
    client.namespace = "prodavan-sandboxes"
    client.auth = object()
    client.get_pod = AsyncMock(return_value=_running_pod())

    payload = {
        "entries": [
            {
                "name": "AGENTS.md",
                "path": "AGENTS.md",
                "kind": "file",
                "size": 12,
                "modified_at": "2026-01-01T00:00:00+00:00",
            }
        ]
    }

    async def _exec(**kwargs):
        _ = kwargs
        return ExecResult(stdout=json.dumps(payload).encode(), stderr=b"", exit_code=0)

    monkeypatch.setattr(
        "prodavan.application.pod_service.adapters.k8s.workspace_exec.exec_in_pod",
        _exec,
    )

    adapter = K8sExecWorkspaceAdapter(client=client)
    entries = await adapter.list_entries(runtime_ref="pod-demo", path="")
    assert len(entries) == 1
    assert entries[0].name == "AGENTS.md"
    assert entries[0].kind == "file"


@pytest.mark.asyncio
async def test_list_entries_maps_exec_transport_error(monkeypatch) -> None:
    pytest.importorskip("websockets")
    from websockets.exceptions import InvalidStatus

    client = AsyncMock()
    client.namespace = "prodavan-sandboxes"
    client.auth = object()
    client.get_pod = AsyncMock(return_value=_running_pod())

    class _Resp:
        status_code = 403

    async def _exec(**kwargs):
        _ = kwargs
        raise InvalidStatus(_Resp())

    monkeypatch.setattr(
        "prodavan.application.pod_service.adapters.k8s.workspace_exec.exec_in_pod",
        _exec,
    )

    adapter = K8sExecWorkspaceAdapter(client=client)
    from prodavan.domain.errors import AppError

    with pytest.raises(AppError) as exc_info:
        await adapter.list_entries(runtime_ref="pod-demo", path="")
    assert exc_info.value.code == "POD_EXEC_FORBIDDEN"
    assert exc_info.value.status == 403


@pytest.mark.asyncio
async def test_read_bytes_returns_stdout(monkeypatch) -> None:
    client = AsyncMock()
    client.namespace = "prodavan-sandboxes"
    client.auth = object()
    client.get_pod = AsyncMock(return_value=_running_pod())

    async def _exec(**kwargs):
        _ = kwargs
        return ExecResult(stdout=b"hello", stderr=b"", exit_code=0)

    monkeypatch.setattr(
        "prodavan.application.pod_service.adapters.k8s.workspace_exec.exec_in_pod",
        _exec,
    )

    adapter = K8sExecWorkspaceAdapter(client=client)
    data = await adapter.read_bytes(runtime_ref="pod-demo", path="AGENTS.md", max_bytes=1024)
    assert data == b"hello"
