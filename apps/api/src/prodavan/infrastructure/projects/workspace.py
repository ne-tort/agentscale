"""Project workspace FS layout writer (L07) + object-store dual-write (P0)."""

from __future__ import annotations

import json
import shutil
import zipfile
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any

from prodavan.config.settings import settings
from prodavan.core.infra.object_keys import workspace_object_key
from prodavan.infrastructure.files.manager import ensure_file_store


def _zip_member_relpath(filename: str, *, package_name: str) -> str | None:
    """Map zip member → path under packages/{name}/ (flat root preferred)."""
    rel = (filename or "").replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    rel = rel.lstrip("/")
    if not rel or rel.endswith("/"):
        return None
    parts = [p for p in rel.split("/") if p and p != "."]
    if not parts or any(p == ".." for p in parts):
        return None
    # Nested zip root packages/{name}/{name}/file → strip one matching prefix.
    if len(parts) >= 2 and parts[0] == package_name:
        parts = parts[1:]
    if not parts:
        return None
    return "/".join(parts)


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
        # Only always-on agent dirs; rules/skills/prompts created on write when needed.
        for rel in ("packages", "inbox", "out", "cabinet-seed"):
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
        # Do not write CLAUDE.md alias — only AGENTS.md.

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
        """Persist zip + extracted tree into object store (hydrate SoT), and locally."""
        from prodavan.infrastructure.projects.mcp_sandbox import stop_all_package_processes

        stop_all_package_processes(workspace_root=self._root)
        names: list[str] = []
        for pkg_name, raw in artifacts:
            safe = Path(pkg_name).name
            # Persist zip in object store (artifact / ensure_package_tree fallback).
            self._put_workspace_bytes(
                f"packages/{safe}.zip",
                raw,
                content_type="application/zip",
            )
            # Hydrate reads ONLY object store — each file must be put, not only local extract.
            with zipfile.ZipFile(BytesIO(raw)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    inner = _zip_member_relpath(info.filename, package_name=safe)
                    if inner is None:
                        continue
                    data = zf.read(info)
                    self._put_workspace_bytes(
                        f"packages/{safe}/{inner}",
                        data,
                        content_type="application/octet-stream",
                    )
            dest = self._root / "packages" / safe
            if dest.exists():
                shutil.rmtree(dest)
            dest.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(BytesIO(raw)) as zf:
                zf.extractall(dest)
            # Flatten accidental packages/{name}/{name}/… from nested zip roots.
            nested = dest / safe
            if nested.is_dir() and not (dest / "server.py").exists() and (nested / "server.py").exists():
                for child in nested.iterdir():
                    target = dest / child.name
                    if target.exists():
                        if target.is_dir():
                            shutil.rmtree(target)
                        else:
                            target.unlink()
                    shutil.move(str(child), str(target))
                shutil.rmtree(nested, ignore_errors=True)
            names.append(safe)
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
        # Audit META-P2b: defensive guard — even if a meta-slug slips through
        # with an unsafe prefix (.., /, .), refuse to prune outside the project
        # workspace. ``workspace_object_key`` raises on ``..`` for the object
        # store, but the local FS path (self._root / rel) needs its own guard
        # so ``shutil.rmtree`` cannot escape the project root.
        rel = prefix.replace("\\", "/").lstrip("/")
        if not rel or rel in {".", "./"}:
            # Wiping the whole workspace is never what a module prune wants.
            return
        if ".." in PurePosixPath(rel).parts:
            return
        if not rel.endswith("/"):
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
