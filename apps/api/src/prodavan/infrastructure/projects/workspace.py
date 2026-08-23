"""Project workspace FS layout writer (L07)."""

from __future__ import annotations

import json
import shutil
import zipfile
from io import BytesIO
from pathlib import Path

from prodavan.config.settings import settings


class WorkspaceLayoutWriter:
    """Idempotent /workspace layout per container.md."""

    def __init__(self, *, workspace_key: str) -> None:
        self._root = Path(settings.storage_root) / "projects" / workspace_key / "workspace"
        self._project_root = Path(settings.storage_root) / "projects" / workspace_key

    @property
    def workspace_root(self) -> Path:
        return self._root

    @property
    def mcp_config_path(self) -> Path:
        return self._root / "mcp.json"

    def ensure_dirs(self) -> None:
        for rel in ("prompts", "rules", "skills", "packages", "inbox", "out", "cabinet-seed"):
            (self._root / rel).mkdir(parents=True, exist_ok=True)

    def write_agents(self, *, cabinet_name: str, project_name: str, agents_md: str | None) -> None:
        text = agents_md or (
            f"# {project_name}\n\n"
            f"Project workspace for cabinet **{cabinet_name}**.\n\n"
            "Edit context in the cabinet UI; re-materialize to refresh.\n"
        )
        (self._root / "AGENTS.md").write_text(text, encoding="utf-8")
        (self._root / "CLAUDE.md").write_text(text, encoding="utf-8")

    def write_mcp_config(self, *, cabinet_id: str, package_names: list[str]) -> None:
        payload = {
            "version": 1,
            "platform": {
                "cabinet_id": cabinet_id,
                "tools_endpoint": f"/api/v1/cabinets/{cabinet_id}/mcp/call",
            },
            "packages": [{"name": n, "root": f"packages/{n}"} for n in package_names],
        }
        self.mcp_config_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def extract_packages(self, artifacts: list[tuple[str, bytes]]) -> list[str]:
        names: list[str] = []
        for pkg_name, raw in artifacts:
            dest = self._root / "packages" / pkg_name
            if dest.exists():
                shutil.rmtree(dest)
            dest.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(BytesIO(raw)) as zf:
                zf.extractall(dest)
            names.append(pkg_name)
        return names

    def store_inbox_attachment(self, *, filename: str, raw: bytes) -> Path:
        safe = Path(filename).name
        path = self._root / "inbox" / safe
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path

    def remove_project_tree(self) -> None:
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
