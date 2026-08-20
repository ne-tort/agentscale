"""Local filesystem storage for cabinet seeds."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from prodavan.config.settings import settings


def cabinet_storage_path(tenant_id: uuid.UUID, cabinet_id: uuid.UUID) -> Path:
    return settings.storage_root / "cabinets" / str(tenant_id) / str(cabinet_id)


def ensure_cabinet_storage(tenant_id: uuid.UUID, cabinet_id: uuid.UUID) -> Path:
    path = cabinet_storage_path(tenant_id, cabinet_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_cabinet_marker(
    tenant_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    *,
    profile_id: str,
    profile_version: str,
    capabilities: dict,
) -> None:
    root = ensure_cabinet_storage(tenant_id, cabinet_id)
    marker = {
        "tenant_id": str(tenant_id),
        "cabinet_id": str(cabinet_id),
        "profile_id": profile_id,
        "profile_version": profile_version,
        "capabilities": capabilities,
    }
    tmp = root / ".cabinet.json.tmp"
    final = root / ".cabinet.json"
    tmp.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(final)


def write_seed_complete(tenant_id: uuid.UUID, cabinet_id: uuid.UUID) -> None:
    root = cabinet_storage_path(tenant_id, cabinet_id)
    (root / ".seed-complete").write_text("ok", encoding="utf-8")


def copy_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def remove_cabinet_storage(tenant_id: uuid.UUID, cabinet_id: uuid.UUID) -> None:
    path = cabinet_storage_path(tenant_id, cabinet_id)
    if path.exists():
        shutil.rmtree(path)
