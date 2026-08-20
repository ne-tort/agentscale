"""Cabinet prompt filesystem storage."""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from prodavan.config.settings import settings

MAX_FILE_BYTES = 512 * 1024
ALLOWED_EXTENSIONS = {".md", ".json"}
RESERVED_TOP_LEVEL = {".versions"}


class PromptPathError(Exception):
    pass


def prompts_root(tenant_id: uuid.UUID, cabinet_id: uuid.UUID) -> Path:
    return settings.storage_root / "cabinets" / str(tenant_id) / str(cabinet_id) / "prompts"


def _validate_relative_path(path: str) -> Path:
    normalized = path.replace("\\", "/").strip()
    if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
        raise PromptPathError("PATH_FORBIDDEN")
    rel = Path(normalized)
    if rel.parts and rel.parts[0] in RESERVED_TOP_LEVEL:
        raise PromptPathError("PATH_FORBIDDEN")
    if rel.name == ".prompts-version.json":
        raise PromptPathError("PATH_FORBIDDEN")
    if rel.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise PromptPathError("PATH_FORBIDDEN")
    return rel


def _is_working_file(root: Path, item: Path) -> bool:
    rel = item.relative_to(root)
    if rel.parts and rel.parts[0] in RESERVED_TOP_LEVEL:
        return False
    if rel.name == ".prompts-version.json":
        return False
    return item.suffix.lower() in ALLOWED_EXTENSIONS


def _digest_content(content: str | bytes) -> str:
    data = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(data).hexdigest()


def _etag_from_digest(digest: str) -> str:
    return f'"{digest[:16]}"'


def _file_info(full: Path, rel: Path) -> dict:
    data = full.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    mtime = datetime.fromtimestamp(full.stat().st_mtime, tz=UTC)
    return {
        "path": rel.as_posix(),
        "content": data.decode("utf-8"),
        "sha256": digest,
        "size_bytes": len(data),
        "updated_at": mtime,
        "etag": _etag_from_digest(digest),
    }


def _versions_dir(root: Path) -> Path:
    return root / ".versions"


def _version_state_path(root: Path) -> Path:
    return root / ".prompts-version.json"


def load_version_state(root: Path) -> dict:
    path = _version_state_path(root)
    if not path.exists():
        return {
            "current_version_id": None,
            "working_tree_dirty": False,
            "last_saved_at": None,
        }
    return json.loads(path.read_text(encoding="utf-8"))


def save_version_state(root: Path, *, current_version_id: str | None, dirty: bool) -> None:
    state = load_version_state(root)
    state["current_version_id"] = current_version_id
    state["working_tree_dirty"] = dirty
    state["last_saved_at"] = datetime.now(UTC).isoformat()
    _version_state_path(root).write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _mark_dirty(root: Path) -> None:
    save_version_state(root, current_version_id=load_version_state(root).get("current_version_id"), dirty=True)


def build_prompt_tree(root: Path, prefix: str = "") -> list[dict]:
    if not root.exists():
        return []
    items: list[dict] = []
    for entry in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if entry.name in RESERVED_TOP_LEVEL or entry.name == ".prompts-version.json":
            continue
        rel = f"{prefix}{entry.name}" if not prefix else f"{prefix}{entry.name}"
        if entry.is_dir():
            children = build_prompt_tree(entry, f"{rel}/")
            items.append({"path": f"{rel}/", "type": "dir", "children": children})
        elif entry.suffix.lower() in ALLOWED_EXTENSIONS:
            items.append({"path": rel, "type": "file", "size": entry.stat().st_size})
    return items


def read_prompt_file(tenant_id: uuid.UUID, cabinet_id: uuid.UUID, path: str) -> dict:
    rel = _validate_relative_path(path)
    full = prompts_root(tenant_id, cabinet_id) / rel
    if not full.exists() or not full.is_file():
        raise PromptPathError("FILE_NOT_FOUND")
    if full.stat().st_size > MAX_FILE_BYTES:
        raise PromptPathError("FILE_TOO_LARGE")
    info = _file_info(full, rel)
    return {
        "path": info["path"],
        "content": info["content"],
        "sha256": info["sha256"],
        "updated_at": info["updated_at"],
        "etag": info["etag"],
    }


def _etag_matches(current_etag: str, expected: str) -> bool:
    normalized = expected.strip()
    if normalized.startswith("W/"):
        normalized = normalized[2:].strip()
    return current_etag == normalized or current_etag.strip('"') == normalized.strip('"')


def write_prompt_file(
    tenant_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    path: str,
    content: str,
    *,
    expected_etag: str | None = None,
) -> dict:
    rel = _validate_relative_path(path)
    root = prompts_root(tenant_id, cabinet_id)
    full = root / rel

    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise PromptPathError("FILE_TOO_LARGE")

    if expected_etag is not None:
        if not full.exists():
            raise PromptPathError("ETAG_MISMATCH")
        current = read_prompt_file(tenant_id, cabinet_id, path)
        if not _etag_matches(current["etag"], expected_etag):
            raise PromptPathError("ETAG_MISMATCH")

    full.parent.mkdir(parents=True, exist_ok=True)
    tmp = full.with_suffix(full.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    tmp.replace(full)
    _mark_dirty(root)
    return read_prompt_file(tenant_id, cabinet_id, path)


def _collect_working_files(root: Path) -> list[dict]:
    manifests: list[dict] = []
    if not root.exists():
        return manifests
    for item in root.rglob("*"):
        if not item.is_file() or not _is_working_file(root, item):
            continue
        rel = item.relative_to(root)
        data = item.read_bytes()
        manifests.append(
            {
                "path": rel.as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
        )
    return sorted(manifests, key=lambda entry: entry["path"])


def _new_version_id() -> str:
    return f"ver_{secrets.token_hex(6)}"


def create_version_snapshot(
    tenant_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    *,
    label: str | None,
    created_by: uuid.UUID,
    parent_version_id: str | None = None,
) -> dict:
    root = prompts_root(tenant_id, cabinet_id)
    version_id = _new_version_id()
    version_dir = _versions_dir(root) / version_id
    files_dir = version_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    file_manifests = _collect_working_files(root)
    for entry in file_manifests:
        src = root / entry["path"]
        dst = files_dir / entry["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    manifest = {
        "version_id": version_id,
        "cabinet_id": str(cabinet_id),
        "label": label,
        "parent_version_id": parent_version_id,
        "created_at": datetime.now(UTC).isoformat(),
        "created_by": str(created_by),
        "files": file_manifests,
    }
    (version_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    save_version_state(root, current_version_id=version_id, dirty=False)
    return manifest


def list_version_manifests(tenant_id: uuid.UUID, cabinet_id: uuid.UUID) -> list[dict]:
    root = prompts_root(tenant_id, cabinet_id)
    versions_root = _versions_dir(root)
    if not versions_root.exists():
        return []
    manifests: list[dict] = []
    for entry in versions_root.iterdir():
        manifest_path = entry / "manifest.json"
        if entry.is_dir() and manifest_path.exists():
            manifests.append(json.loads(manifest_path.read_text(encoding="utf-8")))
    manifests.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return manifests


def get_version_manifest(
    tenant_id: uuid.UUID, cabinet_id: uuid.UUID, version_id: str
) -> dict:
    root = prompts_root(tenant_id, cabinet_id)
    manifest_path = _versions_dir(root) / version_id / "manifest.json"
    if not manifest_path.exists():
        raise PromptPathError("VERSION_NOT_FOUND")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def rollback_to_version(
    tenant_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    version_id: str,
    *,
    created_by: uuid.UUID,
    create_backup: bool = True,
) -> dict:
    root = prompts_root(tenant_id, cabinet_id)
    manifest = get_version_manifest(tenant_id, cabinet_id, version_id)
    files_dir = _versions_dir(root) / version_id / "files"

    if create_backup:
        create_version_snapshot(
            tenant_id,
            cabinet_id,
            label=f"backup before rollback to {version_id}",
            created_by=created_by,
            parent_version_id=load_version_state(root).get("current_version_id"),
        )

    for entry in manifest["files"]:
        src = files_dir / entry["path"]
        dst = root / entry["path"]
        if not src.exists():
            raise PromptPathError("VERSION_CORRUPT")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    return create_version_snapshot(
        tenant_id,
        cabinet_id,
        label=f"rollback to {version_id}",
        created_by=created_by,
        parent_version_id=version_id,
    )
