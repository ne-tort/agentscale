"""Unit tests — PodWorkspaceService gates and preview."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prodavan.application.pod_service.ports.workspace import WorkspaceEntry
from prodavan.application.pod_service.workspace_service import PodWorkspaceService
from prodavan.domain.agent.errors import pod_not_running
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal


def _principal() -> Principal:
    return Principal(sub="emp:test", roles=frozenset({"employee"}))


class _FakeWorkspace:
    def __init__(self) -> None:
        self.created_directories: list[str] = []

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
        if path in self.created_directories:
            return WorkspaceEntry(name=path.rsplit("/", 1)[-1], path=path, kind="dir", size=None, modified_at=None)
        if path == "AGENTS.md":
            return WorkspaceEntry(name="AGENTS.md", path=path, kind="file", size=9, modified_at=None)
        raise AppError(code="WORKSPACE_NOT_FOUND", title="Not Found", status=404, detail="missing")

    async def create_directory(self, *, runtime_ref: str, path: str) -> None:
        if path == "AGENTS.md" or path in self.created_directories:
            raise AppError(
                code="WORKSPACE_ALREADY_EXISTS",
                title="Conflict",
                status=409,
                detail="workspace path already exists",
                extra={"path": path},
            )
        self.created_directories.append(path)

    async def write_bytes(self, *, runtime_ref: str, path: str, data: bytes) -> None:
        return None

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
    with patch(
        "prodavan.application.pod_service.workspace_service.require_running_pod_runtime",
        new=AsyncMock(side_effect=pod_not_running()),
    ):
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
    with patch(
        "prodavan.application.pod_service.workspace_service.require_running_pod_runtime",
        new=AsyncMock(
            return_value={
                "observed_state": "running",
                "stub": False,
                "k8s_pod_name": "pod-demo",
            }
        ),
    ):
        out = await svc.list_entries(
            project_id="proj_x",
            path="",
            principal=_principal(),
            employee=None,
        )
    assert out["path"] == ""
    assert out["entries"][0]["name"] == "AGENTS.md"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["docs", "docs/reference"])
async def test_create_directory_uses_writable_runtime_and_returns_normalized_path(path: str) -> None:
    class _MissingWorkspace(_FakeWorkspace):
        async def stat(self, *, runtime_ref: str, path: str) -> WorkspaceEntry:
            raise AppError(code="WORKSPACE_NOT_FOUND", title="Not Found", status=404, detail="missing")

    workspace = _MissingWorkspace()
    gate = AsyncMock(return_value={"observed_state": "running", "stub": False, "runtime_ref": "pod-demo"})
    svc = PodWorkspaceService(MagicMock(), workspace=workspace)
    with patch("prodavan.application.pod_service.workspace_service.require_running_pod_runtime", new=gate):
        result = await svc.create_directory(
            project_id="proj_x", path=f"./{path}", principal=_principal(), employee=None
        )
    assert result == {"created": True, "path": path}
    assert workspace.created_directories == [path]
    assert gate.await_args is not None
    assert gate.await_args.kwargs["write"] is True


@pytest.mark.asyncio
async def test_create_directory_rejects_file_or_directory_collision() -> None:
    workspace = _FakeWorkspace()
    svc = PodWorkspaceService(MagicMock(), workspace=workspace)
    with patch(
        "prodavan.application.pod_service.workspace_service.require_running_pod_runtime",
        new=AsyncMock(return_value={"observed_state": "running", "stub": False, "runtime_ref": "pod-demo"}),
    ):
        with pytest.raises(AppError) as exc:
            await svc.create_directory(
                project_id="proj_x", path="AGENTS.md", principal=_principal(), employee=None
            )
    assert exc.value.code == "WORKSPACE_ALREADY_EXISTS"
    assert exc.value.status == 409
    assert exc.value.extra == {"path": "AGENTS.md"}
    assert workspace.created_directories == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("code", "status"), [("PROJECT_PAUSED", 409), ("POD_NOT_RUNNING", 409)])
async def test_create_directory_rejects_paused_or_unavailable_pod(code: str, status: int) -> None:
    workspace = _FakeWorkspace()
    svc = PodWorkspaceService(MagicMock(), workspace=workspace)
    with patch(
        "prodavan.application.pod_service.workspace_service.require_running_pod_runtime",
        new=AsyncMock(side_effect=AppError(code=code, title="Conflict", status=status, detail="not writable")),
    ):
        with pytest.raises(AppError) as exc:
            await svc.create_directory(
                project_id="proj_x", path="docs", principal=_principal(), employee=None
            )
    assert exc.value.code == code
    assert workspace.created_directories == []


@pytest.mark.asyncio
async def test_create_directory_rejects_traversal_before_workspace_mutation() -> None:
    workspace = _FakeWorkspace()
    svc = PodWorkspaceService(MagicMock(), workspace=workspace)
    with patch(
        "prodavan.application.pod_service.workspace_service.require_running_pod_runtime",
        new=AsyncMock(return_value={"observed_state": "running", "stub": False, "runtime_ref": "pod-demo"}),
    ):
        with pytest.raises(AppError) as exc:
            await svc.create_directory(
                project_id="proj_x", path="%252e%252e%252fsecret", principal=_principal(), employee=None
            )
    assert exc.value.status == 422
    assert workspace.created_directories == []


@pytest.mark.asyncio
async def test_create_directory_rejects_root_and_overlong_path() -> None:
    workspace = _FakeWorkspace()
    svc = PodWorkspaceService(MagicMock(), workspace=workspace)
    with patch(
        "prodavan.application.pod_service.workspace_service.require_running_pod_runtime",
        new=AsyncMock(return_value={"observed_state": "running", "stub": False, "runtime_ref": "pod-demo"}),
    ):
        for path in (".", "a" * 501):
            with pytest.raises(AppError) as exc:
                await svc.create_directory(
                    project_id="proj_x", path=path, principal=_principal(), employee=None
                )
            assert exc.value.code == "VALIDATION_ERROR"
            assert exc.value.status == 422
    assert workspace.created_directories == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["move", "copy"])
async def test_mutations_reject_root_source_or_destination(operation: str) -> None:
    session = MagicMock()
    workspace = _FakeWorkspace()
    workspace.move = AsyncMock()
    workspace.copy = AsyncMock()
    svc = PodWorkspaceService(session, workspace=workspace)
    with patch(
        "prodavan.application.pod_service.workspace_service.require_running_pod_runtime",
        new=AsyncMock(
            return_value={"observed_state": "running", "stub": False, "runtime_ref": "pod-demo"}
        ),
    ):
        method = getattr(svc, f"{operation}_entry")
        with pytest.raises(AppError) as exc:
            await method(
                project_id="proj_x",
                src=".",
                dst="notes.txt",
                principal=_principal(),
                employee=None,
            )
    assert exc.value.code == "VALIDATION_ERROR"
    assert exc.value.status == 422
    getattr(workspace, operation).assert_not_awaited()


@pytest.mark.asyncio
async def test_mutations_reject_encoded_parent_segments() -> None:
    session = MagicMock()
    workspace = _FakeWorkspace()
    workspace.move = AsyncMock()
    svc = PodWorkspaceService(session, workspace=workspace)
    with patch(
        "prodavan.application.pod_service.workspace_service.require_running_pod_runtime",
        new=AsyncMock(
            return_value={"observed_state": "running", "stub": False, "runtime_ref": "pod-demo"}
        ),
    ):
        with pytest.raises(AppError) as exc:
            await svc.move_entry(
                project_id="proj_x",
                src="%252e%252e%252fsecret.txt",
                dst=r"safe\copy.txt",
                principal=_principal(),
                employee=None,
            )
    assert exc.value.status == 422
    workspace.move.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_rejects_oversized_file_before_reading() -> None:
    session = MagicMock()
    workspace = _FakeWorkspace()
    workspace.read_bytes = AsyncMock(side_effect=AssertionError("oversized file must not be read"))
    svc = PodWorkspaceService(session, workspace=workspace)
    with patch(
        "prodavan.application.pod_service.workspace_service.require_running_pod_runtime",
        new=AsyncMock(
            return_value={"observed_state": "running", "stub": False, "runtime_ref": "pod-demo"}
        ),
    ):
        with pytest.raises(AppError) as exc:
            await svc.read_content(
                project_id="proj_x",
                path="AGENTS.md",
                principal=_principal(),
                employee=None,
                max_bytes=4,
            )
    assert exc.value.code == "PAYLOAD_TOO_LARGE"
    assert exc.value.status == 413
    assert exc.value.extra == {"max_bytes": 4, "size": 9}
    workspace.read_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_text_rejects_binary() -> None:
    session = MagicMock()

    class _BinWorkspace(_FakeWorkspace):
        async def read_bytes(self, *, runtime_ref: str, path: str, max_bytes: int) -> bytes:
            return b"\x00binary"

        async def stat(self, *, runtime_ref: str, path: str) -> WorkspaceEntry:
            return WorkspaceEntry(name="blob.bin", path=path, kind="file", size=7, modified_at=None)

    svc = PodWorkspaceService(session, workspace=_BinWorkspace())
    with patch(
        "prodavan.application.pod_service.workspace_service.require_running_pod_runtime",
        new=AsyncMock(
            return_value={"observed_state": "running", "stub": False, "runtime_ref": "pod-demo"}
        ),
    ):
        with pytest.raises(AppError) as exc:
            await svc.preview_text(
                project_id="proj_x",
                path="blob.bin",
                principal=_principal(),
                employee=None,
            )
    assert exc.value.status == 415
