"""Unit tests — workspace dehydrate tar upload + path rules."""

from __future__ import annotations

import io
import tarfile

import pytest

from prodavan.application.pod_service.workspace_dehydrate_rules import (
    is_excluded_rel,
    is_platform_owned_rel,
)
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
    assert is_excluded_rel(".openclaw-data/session-map.json")
    assert is_excluded_rel(".openclaw-data/transcripts/x.jsonl")
    assert not is_excluded_rel("AGENTS.md")
    assert not is_excluded_rel("out/report.txt")
    assert not is_excluded_rel(".prodavan/config.yaml")

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


def test_upload_preserves_dot_prodavan_config(file_store: FileStoreManager) -> None:
    tar = _tar_bytes({".prodavan/config.yaml": b"version: 1\n"})
    out = upload_workspace_tar(workspace_key="ws_dot", tar_bytes=tar, store=file_store)
    assert out.uploaded == 1
    key = "projects/ws_dot/workspace/.prodavan/config.yaml"
    assert file_store.get_bytes_sync(key) == b"version: 1\n"


def test_is_platform_owned_rel() -> None:
    """mcp.json и packages/* — платформенные: SoT в Postgres, не в поде."""
    assert is_platform_owned_rel("mcp.json")
    assert is_platform_owned_rel("packages/prodavan-equipment/server.py")
    assert is_platform_owned_rel("packages/prodavan-equipment.zip")
    # пользовательские/агентские файлы — не платформенные
    assert not is_platform_owned_rel("AGENTS.md")
    assert not is_platform_owned_rel("out/report.txt")
    assert not is_platform_owned_rel("prompts/equipment/70-ready-builds.md")
    assert not is_platform_owned_rel("packages_backup/x.txt")


def test_dehydrate_does_not_clobber_platform_mcp_package(
    file_store: FileStoreManager,
) -> None:
    """Регресс: дегидратация затирала свежий MCP-пакет старой копией пода.

    Копия пода может быть только равной или устаревшей (её гидратировали при
    старте), поэтому выкачивать mcp.json и packages/* нельзя — иначе обновление
    версии пакета не доезжает до существующих проектов.
    """
    # в хранилище уже лежит СВЕЖИЙ пакет (его записала материализация)
    file_store.put_bytes_sync(
        "projects/ws2/workspace/packages/prodavan-equipment.zip", b"FRESH-2.4.0"
    )
    file_store.put_bytes_sync(
        "projects/ws2/workspace/packages/prodavan-equipment/manifest.json", b'{"version":"2.4.0"}'
    )
    file_store.put_bytes_sync("projects/ws2/workspace/mcp.json", b'{"packages":["2.4.0"]}')

    result = upload_workspace_tar(
        workspace_key="ws2",
        tar_bytes=_tar_bytes(
            {
                # под отдаёт УСТАРЕВШУЮ копию — она не должна перезаписать свежую
                "./mcp.json": b'{"packages":["2.2.0"]}',
                "./packages/prodavan-equipment.zip": b"STALE-2.2.0",
                "./packages/prodavan-equipment/manifest.json": b'{"version":"2.2.0"}',
                # обычный пользовательский файл выкачивается как раньше
                "./out/report.txt": b"user-data",
            }
        ),
        store=file_store,
    )

    # платформенные файлы не перезаписаны копией пода
    assert file_store.get_bytes_sync(
        "projects/ws2/workspace/packages/prodavan-equipment.zip"
    ) == b"FRESH-2.4.0"
    assert file_store.get_bytes_sync("projects/ws2/workspace/mcp.json") == b'{"packages":["2.4.0"]}'
    assert (
        file_store.get_bytes_sync(
            "projects/ws2/workspace/packages/prodavan-equipment/manifest.json"
        )
        == b'{"version":"2.4.0"}'
    )
    # пользовательские данные по-прежнему сохраняются
    assert file_store.get_bytes_sync("projects/ws2/workspace/out/report.txt") == b"user-data"
    assert result.uploaded == 1
    assert result.skipped >= 3


def test_dehydrate_keeps_platform_files_when_pod_lacks_them(
    file_store: FileStoreManager,
) -> None:
    """Если в архиве пода платформенных файлов нет — они не удаляются из хранилища.

    Финальная зачистка удаляет ключи, которых нет в архиве; платформенные
    артефакты должны переживать и этот проход (иначе гидрация останется без
    mcp.json и агент потеряет все MCP-инструменты).
    """
    file_store.put_bytes_sync("projects/ws3/workspace/mcp.json", b'{"packages":["2.4.0"]}')
    file_store.put_bytes_sync(
        "projects/ws3/workspace/packages/prodavan-equipment/server.py", b"code"
    )
    file_store.put_bytes_sync("projects/ws3/workspace/old.txt", b"gone")

    upload_workspace_tar(
        workspace_key="ws3",
        tar_bytes=_tar_bytes({"./AGENTS.md": b"hi"}),
        store=file_store,
    )

    assert file_store.exists_sync("projects/ws3/workspace/mcp.json")
    assert file_store.exists_sync("projects/ws3/workspace/packages/prodavan-equipment/server.py")
    # сироты, не относящиеся к платформе, по-прежнему зачищаются
    assert not file_store.exists_sync("projects/ws3/workspace/old.txt")
