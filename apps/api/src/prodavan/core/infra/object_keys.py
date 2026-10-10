"""Object key / storage_ref helpers (C-OBJECT-STORE)."""

from __future__ import annotations

from pathlib import Path


def content_asset_ref(asset_id: str) -> str:
    """Canonical ref for attachment rows backed by Content Service."""
    return f"content://{asset_id.strip()}"


def parse_content_asset_ref(ref: str) -> str:
    raw = (ref or "").strip()
    if not raw.startswith("content://"):
        raise ValueError(f"not a content asset ref: {raw[:32]}")
    asset_id = raw.removeprefix("content://").strip()
    if not asset_id:
        raise ValueError("empty content asset id")
    return asset_id


def object_ref(key: str) -> str:
    """Canonical product blob ref (backend-agnostic)."""
    return f"object://{key.lstrip('/')}"


def inbox_object_key(*, workspace_key: str, filename: str) -> str:
    safe = Path(filename).name
    return f"projects/{workspace_key}/workspace/inbox/{safe}"


def workspace_object_key(*, workspace_key: str, relative_path: str) -> str:
    """Object key under project workspace (no ``..`` segments)."""
    rel = relative_path.replace("\\", "/").lstrip("/")
    parts = Path(rel).parts
    if ".." in parts:
        raise ValueError(f"unsafe workspace relative path: {relative_path}")
    return f"projects/{workspace_key}/workspace/{rel}"


def workspace_meta_object_key(*, workspace_key: str, name: str) -> str:
    """Служебный объект проекта ВНЕ ``workspace/`` — в гидрацию пода не попадает.

    Там живёт, например, манифест материализованных путей: дегидратация читает
    его, чтобы не выкачивать из пода файлы, которые платформа всё равно
    перезаписывает из Postgres.
    """
    safe = Path(name).name
    if not safe or safe.startswith("."):
        raise ValueError(f"unsafe meta name: {name!r}")
    key = (workspace_key or "").strip().strip("/")
    if not key or ".." in key.split("/"):
        raise ValueError(f"unsafe workspace_key: {workspace_key!r}")
    return f"projects/{key}/meta/{safe}"


def cabinet_package_object_key(*, cabinet_id: str, name: str, version: str) -> str:
    return f"cabinet_packages/{cabinet_id}/{name}-{version}.zip"


def cabinet_packages_prefix(cabinet_id: str) -> str:
    """Prefix for all MCP package zips of one cabinet instance."""
    return f"cabinet_packages/{cabinet_id}/"


def project_tree_prefix(workspace_key: str) -> str:
    """Prefix for all blobs of one project workspace tree."""
    key = (workspace_key or "").strip().strip("/")
    if not key or ".." in key.split("/"):
        raise ValueError(f"unsafe workspace_key: {workspace_key!r}")
    return f"projects/{key}/"


def parse_storage_ref(ref: str) -> str:
    """Map storage_ref → object key.

    Supports:
    - ``object://…`` (canonical)
    - ``file://…`` (legacy local SoT)
    """
    raw = (ref or "").strip()
    if not raw:
        raise ValueError("empty storage_ref")
    if raw.startswith("object://"):
        return raw.removeprefix("object://").lstrip("/")
    if raw.startswith("file://"):
        return raw.removeprefix("file://").lstrip("/")
    raise ValueError(f"unsupported storage_ref scheme: {raw[:32]}")
