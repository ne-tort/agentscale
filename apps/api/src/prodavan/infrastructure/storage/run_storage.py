"""Filesystem layout for spec runs (M02). Disk is source of truth."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from prodavan.infrastructure.storage.project_storage import project_root

SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


class RunStorageError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def sanitize_filename(name: str) -> str:
    base = Path(name.replace("\\", "/")).name
    if not SAFE_FILENAME.match(base):
        raise RunStorageError("INVALID_FILENAME", "Unsafe inbox filename")
    return base


def inbox_dir(tenant_id: uuid.UUID, cabinet_id: uuid.UUID, project_id: str) -> Path:
    return project_root(tenant_id, cabinet_id, project_id) / "inbox"


def runs_dir(tenant_id: uuid.UUID, cabinet_id: uuid.UUID, project_id: str) -> Path:
    return project_root(tenant_id, cabinet_id, project_id) / "runs"


def run_dir(
    tenant_id: uuid.UUID, cabinet_id: uuid.UUID, project_id: str, run_id: str
) -> Path:
    return runs_dir(tenant_id, cabinet_id, project_id) / run_id


def status_path(
    tenant_id: uuid.UUID, cabinet_id: uuid.UUID, project_id: str, run_id: str
) -> Path:
    return run_dir(tenant_id, cabinet_id, project_id, run_id) / "status.json"


def artifact_path(
    tenant_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    project_id: str,
    run_id: str,
    name: str,
) -> Path:
    return run_dir(tenant_id, cabinet_id, project_id, run_id) / name


def write_inbox_file(
    tenant_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    project_id: str,
    filename: str,
    data: bytes,
) -> dict:
    safe = sanitize_filename(filename)
    dest = inbox_dir(tenant_id, cabinet_id, project_id) / safe
    dest.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(dest, data)
    digest = hashlib.sha256(data).hexdigest()
    return {"filename": safe, "size_bytes": len(data), "sha256": digest, "path": dest}


def read_inbox_file(
    tenant_id: uuid.UUID, cabinet_id: uuid.UUID, project_id: str, filename: str
) -> Path:
    safe = sanitize_filename(filename)
    path = inbox_dir(tenant_id, cabinet_id, project_id) / safe
    if not path.is_file():
        raise RunStorageError("INBOX_FILE_NOT_FOUND", f"Inbox file missing: {safe}")
    return path


def init_run_dir(
    tenant_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    project_id: str,
    run_id: str,
    *,
    workspace_key: str,
    input_filename: str,
    capabilities_snapshot: dict,
) -> dict:
    root = run_dir(tenant_id, cabinet_id, project_id, run_id)
    (root / "input").mkdir(parents=True, exist_ok=True)
    inbox_file = read_inbox_file(tenant_id, cabinet_id, project_id, input_filename)
    shutil.copy2(inbox_file, root / "input" / inbox_file.name)
    status = {
        "run_id": run_id,
        "workspace_key": workspace_key,
        "input_file": inbox_file.name,
        "phase": "ingest",
        "phase_status": "pending",
        "phase_history": [],
        "stats": {"rows": 0, "line_items": 0, "offers": 0, "needs_review": 0},
        "capabilities_snapshot": capabilities_snapshot,
        "created_at": _now(),
        "updated_at": _now(),
    }
    write_status(tenant_id, cabinet_id, project_id, run_id, status)
    return status


def load_status(
    tenant_id: uuid.UUID, cabinet_id: uuid.UUID, project_id: str, run_id: str
) -> dict:
    path = status_path(tenant_id, cabinet_id, project_id, run_id)
    if not path.is_file():
        raise RunStorageError("RUN_NOT_FOUND", "Run not found")
    return json.loads(path.read_text(encoding="utf-8"))


def write_status(
    tenant_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    project_id: str,
    run_id: str,
    status: dict,
) -> None:
    status["updated_at"] = _now()
    _atomic_write(
        status_path(tenant_id, cabinet_id, project_id, run_id),
        json.dumps(status, ensure_ascii=False, indent=2),
    )


def write_json_artifact(
    tenant_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    project_id: str,
    run_id: str,
    name: str,
    payload: dict,
) -> None:
    _atomic_write(
        artifact_path(tenant_id, cabinet_id, project_id, run_id, name),
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


def read_json_artifact(
    tenant_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    project_id: str,
    run_id: str,
    name: str,
) -> dict | None:
    path = artifact_path(tenant_id, cabinet_id, project_id, run_id, name)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def artifact_exists(
    tenant_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    project_id: str,
    run_id: str,
    name: str,
) -> bool:
    path = artifact_path(tenant_id, cabinet_id, project_id, run_id, name)
    if name == "commerce.sqlite":
        sqlite = project_root(tenant_id, cabinet_id, project_id) / "commerce.sqlite"
        return sqlite.is_file()
    return path.is_file()


def list_run_ids(tenant_id: uuid.UUID, cabinet_id: uuid.UUID, project_id: str) -> list[str]:
    root = runs_dir(tenant_id, cabinet_id, project_id)
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def append_sources_log(
    tenant_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    project_id: str,
    run_id: str,
    line: str,
) -> None:
    path = artifact_path(tenant_id, cabinet_id, project_id, run_id, "sources.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{_now()} {line}\n")


def input_file_path(
    tenant_id: uuid.UUID, cabinet_id: uuid.UUID, project_id: str, run_id: str, filename: str
) -> Path:
    return run_dir(tenant_id, cabinet_id, project_id, run_id) / "input" / sanitize_filename(filename)
