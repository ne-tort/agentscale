"""Unit tests — PodWorkspaceService gates and preview."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from prodavan.application.pod_service.ports.workspace import WorkspaceEntry
from prodavan.application.pod_service.workspace_service import PodWorkspaceService
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal


def _principal() -> Principal:
    return Principal(sub="emp:test", roles=frozenset({"employee"}))


class _FakeWorkspace:
    async def list_entries(self, *, runtime_ref: str, path: str) -> list[WorkspaceEntry]:
        _ = runtime_ref, path
        return [
            WorkspaceEntry(
                name="AGENTS.md",
                path="AGENTS.md",
                kind="file",
                size=12,
                modified_at="2026-01-01T00:00:00+00:00",
            )
        ]

    async def read_bytes(self, *, runtime_ref: str, path: str, max_bytes: int) -> bytes:
        return b"# Agents\n"

    async def stat(self, *, runtime_ref: str, path: str) -> WorkspaceEntry:
        return WorkspaceEntry(
            name="AGENTS.md",
            path=path,
            kind="file",
            size=9,
            modified_at=None,
        )

    async def delete(self, *, runtime_ref: str, path: str) -> None:
        return None

    async def move(self, *, runtime_ref: str, src: str, dst: str) -> None:
        return None

    async def copy(self, *, runtime_ref: str, src: str, dst: str) -> None:
        return None


@pytest.mark.asyncio
async def test_list_entries_requires_running_pod() -> None:
    session = MagicMock()
    svc = PodWorkspaceService(session, workspace=_FakeWorkspace())
    svc._access.require_access = AsyncMock(return_value=MagicMock())  # noqa: SLF001
    svc._pods.runtime_view = AsyncMock(return_value={"observed_state": "paused", "stub": False})  # noqa: SLF001

    with pytest.raises(AppError) as exc:
        await svc.list_entries(
            project_id="proj_x",
            path="",
            principal=_principal(),
            employee=None,
        )
    assert exc.value.code == "POD_NOT_RUNNING"


@pytest.mark.asyncio
async def test_list_entries_when_running() -> None:
    session = MagicMock()
    svc = PodWorkspaceService(session, workspace=_FakeWorkspace())
    svc._access.require_access = AsyncMock(return_value=MagicMock())  # noqa: SLF001
    svc._pods.runtime_view = AsyncMock(  # noqa: SLF001
        return_value={
            "observed_state": "running",
            "stub": False,
            "k8s_pod_name": "pod-demo",
        }
    )

    out = await svc.list_entries(
        project_id="proj_x",
        path="",
        principal=_principal(),
        employee=None,
    )
    assert out["path"] == ""
    assert out["entries"][0]["name"] == "AGENTS.md"


@pytest.mark.asyncio
async def test_preview_text_rejects_binary() -> None:
    session = MagicMock()

    class _BinWorkspace(_FakeWorkspace):
        async def read_bytes(self, *, runtime_ref: str, path: str, max_bytes: int) -> bytes:
            return b"\x00binary"

    svc = PodWorkspaceService(session, workspace=_BinWorkspace())
    svc._access.require_access = AsyncMock(return_value=MagicMock())  # noqa: SLF001
    svc._pods.runtime_view = AsyncMock(  # noqa: SLF001
        return_value={"observed_state": "running", "stub": False, "runtime_ref": "pod-demo"}
    )

    with pytest.raises(AppError) as exc:
        await svc.preview_text(
            project_id="proj_x",
            path="blob.bin",
            principal=_principal(),
            employee=None,
        )
    assert exc.value.status == 415
