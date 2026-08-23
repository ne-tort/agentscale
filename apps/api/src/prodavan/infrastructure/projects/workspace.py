"""Project workspace FS layout writer (L07) + object-store dual-write (P0)."""

from __future__ import annotations

import json
import shutil
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

from prodavan.config.settings import settings
from prodavan.core.infra.object_keys import workspace_object_key
from prodavan.core.infra.object_storage_manager import ensure_object_storage


class WorkspaceLayoutWriter:
    """Idempotent /workspace layout per container.md.

    Text/config blobs go through ObjectStorageManager (local backend = same paths).
    Package sandbox trees remain local extract (agent cwd); zip copy also stored.
    """

    def __init__(self, *, workspace_key: str) -> None:
        self._workspace_key = workspace_key
        self._root = Path(settings.storage_root) / "projects" / workspace_key / "workspace"
        self._project_root = Path(settings.storage_root) / "projects" / workspace_key

    @property
    def workspace_root(self) -> Path:
        return self._root

    @property
    def mcp_config_path(self) -> Path:
        return self._root / "mcp.json"

    def _put_workspace_bytes(self, relative_path: str, data: bytes, *, content_type: str | None = None) -> None:
        key = workspace_object_key(workspace_key=self._workspace_key, relative_path=relative_path)
        ensure_object_storage().put_bytes_sync(key, data, content_type=content_type)

    def ensure_dirs(self) -> None:
        for rel in ("prompts", "rules", "skills", "packages", "inbox", "out", "cabinet-seed"):
            (self._root / rel).mkdir(parents=True, exist_ok=True)

    def write_agents(self, *, cabinet_name: str, project_name: str, agents_md: str | None) -> None:
        text = agents_md or (
            f"# {project_name}\n\n"
            f"Project workspace for cabinet **{cabinet_name}**.\n\n"
            "Edit context in the cabinet UI; re-materialize to refresh.\n"
        )
        raw = text.encode("utf-8")
        self._put_workspace_bytes("AGENTS.md", raw, content_type="text/markdown; charset=utf-8")
        self._put_workspace_bytes("CLAUDE.md", raw, content_type="text/markdown; charset=utf-8")

    def write_mcp_config(self, *, cabinet_id: str, packages: list[dict[str, Any]]) -> None:
        payload = {
            "version": 1,
            "platform": {
                "cabinet_id": cabinet_id,
                "tools_endpoint": f"/api/v1/cabinets/{cabinet_id}/mcp/call",
            },
            "packages": packages,
        }
        raw = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        self._put_workspace_bytes("mcp.json", raw, content_type="application/json")

    def prepare_package_sandboxes(self, package_names: list[str]) -> list[dict[str, Any]]:
        from prodavan.infrastructure.projects.mcp_sandbox import prepare_package_sandbox

        records: list[dict[str, Any]] = []
        for name in package_names:
            self.ensure_package_tree(name)
            records.append(prepare_package_sandbox(workspace_root=self._root, package_name=name))
        return records

    def ensure_package_tree(self, pkg_name: str) -> bool:
        """Ensure extracted package dir exists; hydrate from object-store zip if missing."""
        safe = Path(pkg_name).name
        dest = self._root / "packages" / safe
        if dest.is_dir() and any(dest.iterdir()):
            return True
        key = workspace_object_key(
            workspace_key=self._workspace_key,
            relative_path=f"packages/{safe}.zip",
        )
        try:
            raw = ensure_object_storage().get_bytes_sync(key)
        except FileNotFoundError:
            return False
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(BytesIO(raw)) as zf:
            zf.extractall(dest)
        return True

    def extract_packages(self, artifacts: list[tuple[str, bytes]]) -> list[str]:
        from prodavan.infrastructure.projects.mcp_sandbox import stop_all_package_processes

        stop_all_package_processes(workspace_root=self._root)
        names: list[str] = []
        for pkg_name, raw in artifacts:
            # Persist zip in object store (SoT for package blob in workspace prefix).
            self._put_workspace_bytes(
                f"packages/{pkg_name}.zip",
                raw,
                content_type="application/zip",
            )
            dest = self._root / "packages" / pkg_name
            if dest.exists():
                shutil.rmtree(dest)
            dest.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(BytesIO(raw)) as zf:
                zf.extractall(dest)
            names.append(pkg_name)
        return names

    def store_inbox_attachment(self, *, filename: str, raw: bytes) -> Path:
        """Write inbox blob via ObjectStorageManager (local backend → same path)."""
        from prodavan.core.infra.object_keys import inbox_object_key

        safe = Path(filename).name
        key = inbox_object_key(workspace_key=self._workspace_key, filename=safe)
        ensure_object_storage().put_bytes_sync(key, raw)
        path = self._root / "inbox" / safe
        return path

    def remove_inbox_attachment(self, *, filename: str) -> bool:
        from prodavan.core.infra.object_keys import inbox_object_key

        safe = Path(filename).name
        key = inbox_object_key(workspace_key=self._workspace_key, filename=safe)
        return ensure_object_storage().delete_sync(key)

    def read_inbox_attachment(self, *, filename: str) -> bytes:
        from prodavan.core.infra.object_keys import inbox_object_key

        safe = Path(filename).name
        key = inbox_object_key(workspace_key=self._workspace_key, filename=safe)
        return ensure_object_storage().get_bytes_sync(key)

    def remove_project_tree(self) -> None:
        from prodavan.core.infra.object_storage_manager import ensure_object_storage
        from prodavan.infrastructure.projects.mcp_sandbox import stop_all_package_processes

        if self._root.is_dir():
            stop_all_package_processes(workspace_root=self._root)
        # Object-store SoT: wipe project prefix (S3 and/or local keys).
        try:
            ensure_object_storage().delete_prefix_sync(f"projects/{self._workspace_key}/")
        except Exception:
            # Fall through to local rmtree for transitional FS-only trees.
            pass
        if self._project_root.is_dir():
            shutil.rmtree(self._project_root)


def workspace_tree_bytes(workspace_key: str) -> int:
    """Best-effort on-disk workspace size for admin metrics (L04)."""
    root = Path(settings.storage_root) / "projects" / workspace_key
    if not root.is_dir():
        return 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total
