"""Upload tar stream members into object-store workspace prefix (last-good)."""

from __future__ import annotations

import io
import json
import logging
import mimetypes
import tarfile
from typing import Any, BinaryIO

from prodavan.application.pod_service.ports.dehydrate import DehydrateResult
from prodavan.application.pod_service.workspace_dehydrate_rules import (
    is_excluded_rel,
    is_platform_owned_rel,
    materialized_paths_from_manifest,
    max_file_bytes,
)
from prodavan.core.infra.object_keys import (
    workspace_meta_object_key,
    workspace_object_key,
)
from prodavan.infrastructure.files.manager import ensure_file_store
from prodavan.infrastructure.projects.tar_paths import tar_member_relpath

logger = logging.getLogger(__name__)


def _load_materialized_paths(mgr: Any, workspace_key: str) -> frozenset[str]:
    """Манифест материализованных путей из хранилища (best-effort).

    Пишется `checkpoint_project_workspace` из `projects.materialize_manifest`.
    Отсутствие/повреждение манифеста не ошибка: остаются статические правила
    (`mcp.json`, `packages/`, `.prodavan/`, `AGENTS.md`).
    """
    try:
        raw = mgr.get_bytes_sync(
            workspace_meta_object_key(workspace_key=workspace_key, name="materialized.json")
        )
        data = json.loads(raw or b"null")
    except Exception:  # noqa: BLE001 - манифест не критичен, деградируем тихо
        return frozenset()
    if isinstance(data, dict):
        paths = data.get("paths")
        if isinstance(paths, (list, tuple)):
            return materialized_paths_from_manifest({"_": list(paths)})
        return materialized_paths_from_manifest(data)
    if isinstance(data, list):
        return materialized_paths_from_manifest({"_": data})
    return frozenset()


def upload_workspace_tar(
    *,
    workspace_key: str,
    tar_bytes: bytes | None = None,
    tar_file: BinaryIO | None = None,
    store: Any | None = None,
) -> DehydrateResult:
    """Extract gzip/ustar archive rooted at /workspace and overwrite MinIO last-good.

    ``tar_file`` (binary file-like) lets callers stream large archives from a
    spooled temp file instead of buffering the whole archive in memory;
    ``tar_bytes`` remains the in-memory option.
    """
    mgr = store or ensure_file_store()
    uploaded = 0
    skipped = 0
    kept_keys: set[str] = set()
    cap = max_file_bytes()
    materialized = _load_materialized_paths(mgr, workspace_key)

    if tar_file is not None:
        tar_file.seek(0)
        fileobj: BinaryIO = tar_file
    else:
        fileobj = io.BytesIO(tar_bytes or b"")

    with tarfile.open(fileobj=fileobj, mode="r:*") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            rel = tar_member_relpath(member.name)
            if not rel or is_excluded_rel(rel):
                skipped += 1
                continue
            if is_platform_owned_rel(rel, materialized=materialized):
                # Копия пода не авторитетна: материализованные файлы пишет
                # платформа из Postgres (file_ref / seed-zip / правила модулей).
                # Не выкачиваем их (иначе старая версия затрёт свежую), но
                # помечаем существующий ключ как «оставить», чтобы финальная
                # зачистка не удалила их из хранилища.
                kept_keys.add(
                    workspace_object_key(workspace_key=workspace_key, relative_path=rel)
                )
                skipped += 1
                continue
            if member.size > cap:
                logger.warning(
                    "dehydrate skip oversized file workspace_key=%s rel=%s size=%s",
                    workspace_key,
                    rel,
                    member.size,
                )
                skipped += 1
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                skipped += 1
                continue
            data = extracted.read()
            key = workspace_object_key(workspace_key=workspace_key, relative_path=rel)
            content_type, _ = mimetypes.guess_type(rel)
            mgr.put_bytes_sync(key, data, content_type=content_type or "application/octet-stream")
            kept_keys.add(key)
            uploaded += 1

    prefix = f"projects/{workspace_key}/workspace/"
    existing = mgr.list_prefix_sync(prefix, limit=50_000)
    deleted = 0
    for key in existing:
        if key.endswith("/"):
            continue
        if key in kept_keys:
            continue
        # Платформенные артефакты (mcp.json, packages/*, .prodavan/*,
        # материализованные пути модулей) живут в хранилище и без архива пода:
        # их пишет материализация. Удалять их здесь нельзя — иначе гидрация
        # останется без mcp.json и агент потеряет MCP-инструменты.
        rel = key[len(prefix):] if key.startswith(prefix) else key
        if is_platform_owned_rel(rel, materialized=materialized):
            continue
        if mgr.delete_sync(key):
            deleted += 1

    logger.info(
        "dehydrate uploaded=%s deleted=%s skipped=%s workspace_key=%s",
        uploaded,
        deleted,
        skipped,
        workspace_key,
    )
    return DehydrateResult(uploaded=uploaded, deleted=deleted, skipped=skipped)
