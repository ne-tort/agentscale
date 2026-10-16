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


def test_upload_does_not_clobber_dot_prodavan_config(file_store: FileStoreManager) -> None:
    """Регресс: чекапоинт пода перезатирал свежий конфиг провайдера.

    `.prodavan/config.yaml` пишет материализация из Postgres, а копия пода может
    быть только равной или УСТАРЕВШЕЙ. Когда её выкачивали в хранилище,
    пересозданный под гидрировался прежним `base_url`/`key_ref`: чат показывал
    модели нового провайдера, а вызов падал в 401 «Invalid token».
    """
    fresh = "projects/ws_dot/workspace/.prodavan/config.yaml"
    file_store.put_bytes_sync(fresh, b"provider: grok\n")

    tar = _tar_bytes({".prodavan/config.yaml": b"provider: cheapai\n"})
    out = upload_workspace_tar(workspace_key="ws_dot", tar_bytes=tar, store=file_store)

    assert out.uploaded == 0
    assert file_store.get_bytes_sync(fresh) == b"provider: grok\n"


def test_upload_does_not_clobber_materialized_prompts(file_store: FileStoreManager) -> None:
    """Пути модулей шаблонные ({{target_path}}) — их защищает манифест."""
    import json

    from prodavan.core.infra.object_keys import workspace_meta_object_key

    file_store.put_bytes_sync(
        workspace_meta_object_key(workspace_key="ws_m", name="materialized.json"),
        json.dumps({"paths": ["prompts/equipment/70-ready-builds.md"]}).encode(),
        content_type="application/json",
    )
    fresh = "projects/ws_m/workspace/prompts/equipment/70-ready-builds.md"
    file_store.put_bytes_sync(fresh, b"NEW")

    tar = _tar_bytes(
        {
            "prompts/equipment/70-ready-builds.md": b"OLD",
            "notes/agent.md": b"mine",
        }
    )
    out = upload_workspace_tar(workspace_key="ws_m", tar_bytes=tar, store=file_store)

    assert file_store.get_bytes_sync(fresh) == b"NEW"
    # файл агента по-прежнему выкачивается — манифест не делает всё платформенным
    assert file_store.get_bytes_sync("projects/ws_m/workspace/notes/agent.md") == b"mine"
    assert out.uploaded == 1


def test_is_platform_owned_rel() -> None:
    """mcp.json, packages/*, .prodavan/*, AGENTS.md — платформенные: SoT в Postgres."""
    assert is_platform_owned_rel("mcp.json")
    assert is_platform_owned_rel("packages/prodavan-equipment/server.py")
    assert is_platform_owned_rel("packages/prodavan-equipment.zip")
    assert is_platform_owned_rel(".prodavan/config.yaml")
    assert is_platform_owned_rel("AGENTS.md")
    # пользовательские/агентские файлы — не платформенные
    assert not is_platform_owned_rel("out/report.txt")
    assert not is_platform_owned_rel("packages_backup/x.txt")
    assert not is_platform_owned_rel("agents.md")
    # пути модулей шаблонные — платформенные только когда их назвал манифест
    assert not is_platform_owned_rel("prompts/equipment/70-ready-builds.md")
    assert is_platform_owned_rel(
        "prompts/equipment/70-ready-builds.md",
        materialized=frozenset({"prompts/equipment/70-ready-builds.md"}),
    )
    # запись манифеста может быть каталогом (packages/prodavan-equipment)
    assert is_platform_owned_rel(
        "bundle/x/server.py", materialized=frozenset({"bundle/x"})
    )
    # совпадение по границе сегмента, не по подстроке
    assert not is_platform_owned_rel(
        "prompts/equipment/70-ready-builds.md.bak",
        materialized=frozenset({"prompts/equipment/70-ready-builds.md"}),
    )


def test_materialized_paths_from_manifest() -> None:
    from prodavan.application.pod_service.workspace_dehydrate_rules import (
        materialized_paths_from_manifest,
    )

    manifest = {
        "mod_equipment": [
            "AGENTS.md",
            "prompts/equipment/60-builds.md",
            "packages/prodavan-equipment",
        ],
        "mod_prompts": [],
    }
    assert materialized_paths_from_manifest(manifest) == frozenset(
        {"AGENTS.md", "prompts/equipment/60-builds.md", "packages/prodavan-equipment"}
    )
    # "." или "" объявили бы платформенным ВООБЩЕ всё, включая файлы агента
    assert materialized_paths_from_manifest({"m": [".", "", "../evil"]}) == frozenset()
    assert materialized_paths_from_manifest(None) == frozenset()
    assert materialized_paths_from_manifest({"m": "not-a-list"}) == frozenset()


def test_checkpoint_persists_materialized_manifest(file_store: FileStoreManager) -> None:
    """Чекапоинт кладёт манифест ДО дегидратации — иначе защищать нечем."""
    import json
    from types import SimpleNamespace

    from prodavan.application.projects.workspace_checkpoint import (
        _persist_materialized_manifest,
    )
    from prodavan.core.infra.object_keys import workspace_meta_object_key

    project = SimpleNamespace(
        materialize_manifest={
            "mod_equipment": ["AGENTS.md", "prompts/equipment/60-builds.md"]
        }
    )
    _persist_materialized_manifest(workspace_key="ws_p", project=project)

    raw = file_store.get_bytes_sync(
        workspace_meta_object_key(workspace_key="ws_p", name="materialized.json")
    )
    assert json.loads(raw) == {
        "paths": ["AGENTS.md", "prompts/equipment/60-builds.md"]
    }


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
