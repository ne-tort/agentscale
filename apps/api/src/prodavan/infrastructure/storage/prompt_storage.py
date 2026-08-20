"""Read cabinet prompt files from storage."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

from prodavan.config.settings import settings

FORBIDDEN_PREFIXES = ("../", "..\\", "/")
MAX_FILE_BYTES = 512 * 1024


class PromptPathError(Exception):
    pass


def prompts_root(tenant_id: uuid.UUID, cabinet_id: uuid.UUID) -> Path:
    return settings.storage_root / "cabinets" / str(tenant_id) / str(cabinet_id) / "prompts"


def _validate_relative_path(path: str) -> Path:
    normalized = path.replace("\\", "/").strip()
    if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
        raise PromptPathError("PATH_FORBIDDEN")
    return Path(normalized)


def build_prompt_tree(root: Path, prefix: str = "") -> list[dict]:
    if not root.exists():
        return []
    items: list[dict] = []
    for entry in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        rel = f"{prefix}{entry.name}" if not prefix else f"{prefix}{entry.name}"
        if entry.is_dir():
            children = build_prompt_tree(entry, f"{rel}/")
            items.append({"path": f"{rel}/", "type": "dir", "children": children})
        else:
            items.append({"path": rel, "type": "file", "size": entry.stat().st_size})
    return items


def read_prompt_file(tenant_id: uuid.UUID, cabinet_id: uuid.UUID, path: str) -> dict:
    rel = _validate_relative_path(path)
    full = prompts_root(tenant_id, cabinet_id) / rel
    if not full.exists() or not full.is_file():
        raise PromptPathError("FILE_NOT_FOUND")
    if full.stat().st_size > MAX_FILE_BYTES:
        raise PromptPathError("FILE_TOO_LARGE")
    content = full.read_text(encoding="utf-8")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    mtime = datetime.fromtimestamp(full.stat().st_mtime, tz=UTC)
    return {
        "path": path.replace("\\", "/"),
        "content": content,
        "sha256": digest,
        "updated_at": mtime,
        "etag": f'"{digest[:16]}"',
    }
