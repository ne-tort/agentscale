"""Object key / storage_ref helpers (C-OBJECT-STORE)."""

from __future__ import annotations

from pathlib import Path


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


def cabinet_package_object_key(*, cabinet_id: str, name: str, version: str) -> str:
    return f"cabinet_packages/{cabinet_id}/{name}-{version}.zip"


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
