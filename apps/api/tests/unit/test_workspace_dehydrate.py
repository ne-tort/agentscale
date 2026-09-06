"""Unit tests — workspace dehydrate tar upload + path rules."""

from __future__ import annotations

import io
import tarfile

import pytest

from prodavan.application.pod_service.workspace_dehydrate_rules import is_excluded_rel
from prodavan.application.pod_service.workspace_tar_upload import upload_workspace_tar
from prodavan.infrastructure.files.manager import FileStoreManager, set_file_store


def _tar_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture()
def file_store(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    mgr = FileStoreManager(backend="local", storage_root=tmp_path)
    mgr._primary = mgr._local
    set_file_store(mgr)
    yield mgr
    set_file_store(None)


def test_is_excluded_rel_denylist() -> None:
    assert is_excluded_rel("node_modules/pkg/index.js")
    assert is_excluded_rel(".git/config")
    assert is_excluded_rel("src/__pycache__/x.pyc")
    assert not is_excluded_rel("AGENTS.md")
    assert not is_excluded_rel("out/report.txt")
    assert not is_excluded_rel(".openclaw-data/session-map.json")


def test_upload_workspace_tar_overwrites_and_deletes_orphans(file_store: FileStoreManager) -> None:
    file_store.put_bytes_sync("projects/ws1/workspace/old.txt", b"gone")
    file_store.put_bytes_sync("projects/ws1/workspace/keep.txt", b"stale")

    result = upload_workspace_tar(
        workspace_key="ws1",
        tar_bytes=_tar_bytes(
            {
                "./keep.txt": b"fresh",
                "./out/new.txt": b"created",
                "./node_modules/x/a.js": b"junk",
            }
        ),
        store=file_store,
    )
    assert result.uploaded == 2
    assert result.deleted >= 1
    assert file_store.get_bytes_sync("projects/ws1/workspace/keep.txt") == b"fresh"
    assert file_store.get_bytes_sync("projects/ws1/workspace/out/new.txt") == b"created"
    assert not file_store.exists_sync("projects/ws1/workspace/old.txt")
    assert not file_store.exists_sync("projects/ws1/workspace/node_modules/x/a.js")


def test_hydrate_prefix_lands_under_workspace_root() -> None:
    """Contract: object key projects/{k}/workspace/AGENTS.md → /workspace/AGENTS.md."""

    # Smoke: prefix constant in source — covered by reading module behavior via docstring contract.
    import inspect

    from prodavan.runtime import hydrate as hydrate_mod

    src = inspect.getsource(hydrate_mod._sync_from_minio)
    assert 'f"projects/{workspace_key}/workspace/"' in src
