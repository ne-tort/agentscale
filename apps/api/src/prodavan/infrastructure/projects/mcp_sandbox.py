"""MCP package sandbox prepare + optional local process spawn (L07)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prodavan.config.settings import settings

# Local-ws only: keep Popen handles so stop can terminate reliably (esp. Windows).
_RUNNING: dict[str, subprocess.Popen] = {}


def _run_key(workspace_root: Path, package_name: str) -> str:
    return f"{workspace_root.resolve()}::{package_name}"


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


def _write_run_json(sandbox_dir: Path, record: dict[str, Any]) -> None:
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    (sandbox_dir / "run.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _read_run_json(sandbox_dir: Path) -> dict[str, Any] | None:
    path = sandbox_dir / "run.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # OpenProcess is more reliable than signal 0 on Windows.
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def stop_package_process(*, workspace_root: Path, package_name: str) -> dict[str, Any]:
    """Stop a previously spawned local package process (best-effort)."""
    import time

    sandbox_dir = workspace_root / "packages" / package_name / ".sandbox"
    record = _read_run_json(sandbox_dir) or {"name": package_name}
    key = _run_key(workspace_root, package_name)
    proc = _RUNNING.pop(key, None)
    pid = record.get("pid") if isinstance(record.get("pid"), int) else None
    if proc is not None:
        pid = proc.pid
        try:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        except OSError:
            pass
    elif isinstance(pid, int) and _pid_alive(pid):
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F", "/T"],
                    check=False,
                    capture_output=True,
                )
            else:
                os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        for _ in range(20):
            if not _pid_alive(pid):
                break
            time.sleep(0.05)

    if isinstance(pid, int):
        for _ in range(20):
            if not _pid_alive(pid):
                break
            time.sleep(0.05)

    record["process"] = "stopped"
    record["pid"] = None
    record["stopped_at"] = datetime.now(UTC).isoformat()
    _write_run_json(sandbox_dir, record)
    return record


def stop_all_package_processes(*, workspace_root: Path) -> list[dict[str, Any]]:
    packages_dir = workspace_root / "packages"
    if not packages_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for child in packages_dir.iterdir():
        if child.is_dir():
            out.append(stop_package_process(workspace_root=workspace_root, package_name=child.name))
    return out


def start_package_process(*, workspace_root: Path, package_name: str) -> dict[str, Any]:
    """Spawn local python entry for a ready package (opt-in via MCP_SANDBOX_SPAWN)."""
    sandbox_dir = workspace_root / "packages" / package_name / ".sandbox"
    record = _read_run_json(sandbox_dir)
    if record is None:
        return {"name": package_name, "status": "invalid", "reason": "run.json missing"}
    if record.get("status") != "ready":
        return record
    if not settings.mcp_sandbox_spawn:
        record["process"] = "not_started"
        record["spawn_skipped"] = "mcp_sandbox_spawn=false"
        _write_run_json(sandbox_dir, record)
        return record

    stop_package_process(workspace_root=workspace_root, package_name=package_name)
    record = _read_run_json(sandbox_dir) or record

    entry = record.get("entry") or {}
    args = entry.get("args") if isinstance(entry.get("args"), list) else []
    pkg_root = workspace_root / "packages" / package_name
    log_path = sandbox_dir / "stdout.log"
    try:
        log_fh = log_path.open("ab")
        proc = subprocess.Popen(  # noqa: S603 — entry allowlisted at deploy/prepare
            [sys.executable, *args],
            cwd=str(pkg_root),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        log_fh.close()
    except OSError as exc:
        record["process"] = "failed"
        record["reason"] = str(exc)
        _write_run_json(sandbox_dir, record)
        return record

    _RUNNING[_run_key(workspace_root, package_name)] = proc
    record["process"] = "running"
    record["pid"] = proc.pid
    record["started_at"] = datetime.now(UTC).isoformat()
    record.pop("spawn_skipped", None)
    record.pop("reason", None)
    _write_run_json(sandbox_dir, record)
    return record


def prepare_package_sandbox(*, workspace_root: Path, package_name: str) -> dict[str, Any]:
    """Validate extracted package and write packages/{name}/.sandbox/run.json."""
    stop_package_process(workspace_root=workspace_root, package_name=package_name)

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

    if settings.mcp_sandbox_spawn:
        return start_package_process(workspace_root=workspace_root, package_name=package_name)
    return record
