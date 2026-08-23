"""MCP package sandbox prepare on materialize (L07 subset — no process spawn)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _module_entry_path(package_root: Path, args: list[str]) -> Path | None:
    """Resolve python -m module to a file under package_root."""
    if len(args) < 2 or args[0] != "-m":
        return None
    module = str(args[1])
    parts = module.split(".")
    direct = package_root.joinpath(*parts).with_suffix(".py")
    if direct.is_file():
        return direct
    pkg_init = package_root.joinpath(*parts) / "__init__.py"
    if pkg_init.is_file():
        return pkg_init
    return None


def prepare_package_sandbox(*, workspace_root: Path, package_name: str) -> dict[str, Any]:
    """Validate extracted package and write packages/{name}/.sandbox/run.json."""
    pkg_root = workspace_root / "packages" / package_name
    sandbox_dir = pkg_root / ".sandbox"
    manifest_path = pkg_root / "manifest.json"

    base: dict[str, Any] = {
        "name": package_name,
        "root": f"packages/{package_name}",
        "prepared_at": datetime.now(UTC).isoformat(),
    }

    if not manifest_path.is_file():
        record = {
            **base,
            "status": "invalid",
            "reason": "manifest.json missing after extract",
        }
        _write_run_json(sandbox_dir, record)
        return record

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        record = {**base, "status": "invalid", "reason": "bad manifest.json"}
        _write_run_json(sandbox_dir, record)
        return record

    entry = manifest.get("entry") or {}
    cmd = str(entry.get("command") or "")
    args = entry.get("args") if isinstance(entry.get("args"), list) else []
    args = [str(a) for a in args]

    if cmd != "python":
        record = {**base, "status": "invalid", "reason": "entry.command not supported for local sandbox"}
        _write_run_json(sandbox_dir, record)
        return record

    entry_path = _module_entry_path(pkg_root, args)
    if entry_path is None:
        record = {
            **base,
            "status": "invalid",
            "reason": f"entry module not found: {' '.join(args)}",
            "entry": {"command": cmd, "args": args},
        }
        _write_run_json(sandbox_dir, record)
        return record

    perms = manifest.get("permissions") or {}
    hosts = perms.get("network_hosts") if isinstance(perms.get("network_hosts"), list) else []
    tools = manifest.get("tools") if isinstance(manifest.get("tools"), list) else []
    tool_names = [str(t.get("name")) for t in tools if isinstance(t, dict) and t.get("name")]

    record = {
        **base,
        "status": "ready",
        "runtime": str(manifest.get("runtime") or ""),
        "entry": {"command": cmd, "args": args},
        "entry_module_path": str(entry_path.relative_to(pkg_root)).replace("\\", "/"),
        "tools": tool_names,
        "network_hosts": [str(h) for h in hosts],
        "process": "not_started",
    }
    _write_run_json(sandbox_dir, record)
    return record


def _write_run_json(sandbox_dir: Path, record: dict[str, Any]) -> None:
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    (sandbox_dir / "run.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
