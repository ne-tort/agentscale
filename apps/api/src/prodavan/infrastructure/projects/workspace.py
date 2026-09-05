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
from prodavan.infrastructure.files.manager import ensure_file_store


class WorkspaceLayoutWriter:
    """Idempotent /workspace layout per container.md.

    Text/config blobs go through FileStoreManager (local backend = same paths).
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
        ensure_file_store().put_bytes_sync(key, data, content_type=content_type)

    def ensure_dirs(self) -> None:
        for rel in ("prompts", "rules", "skills", "packages", "inbox", "out", "cabinet-seed"):
            (self._root / rel).mkdir(parents=True, exist_ok=True)

    def write_text_file(self, *, relative_path: str, text: str) -> None:
        rel = relative_path.lstrip("/").replace("\\", "/")
        raw = text.encode("utf-8")
        self._put_workspace_bytes(rel, raw, content_type="text/plain; charset=utf-8")

    def write_bytes_file(self, *, relative_path: str, data: bytes, content_type: str | None = None) -> None:
        rel = relative_path.lstrip("/").replace("\\", "/")
        self._put_workspace_bytes(rel, data, content_type=content_type or "application/octet-stream")

    def read_bytes_file(self, relative_path: str) -> bytes | None:
        """Read workspace blob if present (for materialize idempotency)."""
        rel = relative_path.lstrip("/").replace("\\", "/")
        key = workspace_object_key(workspace_key=self._workspace_key, relative_path=rel)
        try:
            return ensure_file_store().get_bytes_sync(key)
        except FileNotFoundError:
            local = self._root / rel
            if local.is_file():
                return local.read_bytes()
            return None

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
            raw = ensure_file_store().get_bytes_sync(key)
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
        """Write inbox blob via FileStoreManager (local backend → same path)."""
        from prodavan.core.infra.object_keys import inbox_object_key

        safe = Path(filename).name
        key = inbox_object_key(workspace_key=self._workspace_key, filename=safe)
        ensure_file_store().put_bytes_sync(key, raw)
        path = self._root / "inbox" / safe
        return path

    def remove_inbox_attachment(self, *, filename: str) -> bool:
        from prodavan.core.infra.object_keys import inbox_object_key

        safe = Path(filename).name
        key = inbox_object_key(workspace_key=self._workspace_key, filename=safe)
        return ensure_file_store().delete_sync(key)

    def read_inbox_attachment(self, *, filename: str) -> bytes:
        from prodavan.core.infra.object_keys import inbox_object_key

        safe = Path(filename).name
        key = inbox_object_key(workspace_key=self._workspace_key, filename=safe)
        return ensure_file_store().get_bytes_sync(key)

    def remove_relative_path(self, relative_path: str) -> None:
        rel = relative_path.lstrip("/").replace("\\", "/")
        key = workspace_object_key(workspace_key=self._workspace_key, relative_path=rel)
        try:
            ensure_file_store().delete_sync(key)
        except FileNotFoundError:
            pass
        local = self._root / rel
        if local.is_file():
            local.unlink(missing_ok=True)
        elif local.is_dir():
            shutil.rmtree(local, ignore_errors=True)

    def wipe_prefix(self, prefix: str) -> None:
        rel = prefix.lstrip("/").replace("\\", "/")
        if rel and not rel.endswith("/"):
            if "/" not in rel and "." in rel.split("/")[-1]:
                self.remove_relative_path(rel)
                return
            rel = f"{rel}/"
        key_prefix = workspace_object_key(workspace_key=self._workspace_key, relative_path=rel)
        ensure_file_store().delete_prefix_sync(key_prefix)
        local = self._root / rel
        if local.is_dir():
            shutil.rmtree(local, ignore_errors=True)
        elif local.is_file():
            local.unlink(missing_ok=True)

    def remove_project_tree(self) -> dict[str, Any]:
        """Stop MCP sandboxes, wipe object-store prefix (verified), then local FS tree."""
        from prodavan.application.projects.project_wipe import wipe_project_tree
        from prodavan.infrastructure.projects.mcp_sandbox import stop_all_package_processes

        if self._root.is_dir():
            stop_all_package_processes(workspace_root=self._root)
        wipe = wipe_project_tree(self._workspace_key)
        if self._project_root.is_dir():
            shutil.rmtree(self._project_root, ignore_errors=True)
        return wipe


def workspace_tree_bytes(workspace_key: str) -> int:
    """Best-effort full project tree size (``projects/{key}/``), not only ``workspace/``."""
    from prodavan.core.infra.object_keys import project_tree_prefix

    key = (workspace_key or "").strip()
    if not key:
        return 0
    try:
        return ensure_file_store().prefix_size_sync(project_tree_prefix(key))
    except Exception:
        root = Path(settings.storage_root) / "projects" / key
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
