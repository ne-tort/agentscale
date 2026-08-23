"""Unit tests — MCP package sandbox prepare (L07)."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from prodavan.config.settings import settings
from prodavan.infrastructure.cabinets.package_codec import build_minimal_package_zip
from prodavan.infrastructure.projects.mcp_sandbox import (
    _pid_alive,
    prepare_package_sandbox,
    start_package_process,
    stop_package_process,
)
from prodavan.infrastructure.projects.workspace import WorkspaceLayoutWriter


def test_prepare_sandbox_writes_ready_run_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "mcp_sandbox_spawn", False)
    ws_key = "proj_test_sandbox"
    writer = WorkspaceLayoutWriter(workspace_key=str(ws_key))
    writer.ensure_dirs()
    raw = build_minimal_package_zip(name="demo_sync", version="1.0.0")
    writer.extract_packages([("demo_sync", raw)])

    record = prepare_package_sandbox(workspace_root=writer.workspace_root, package_name="demo_sync")
    assert record["status"] == "ready"
    assert record["entry"]["command"] == "python"

    run_path = writer.workspace_root / "packages" / "demo_sync" / ".sandbox" / "run.json"
    assert run_path.is_file()
    on_disk = json.loads(run_path.read_text(encoding="utf-8"))
    assert on_disk["status"] == "ready"
    assert on_disk["process"] == "not_started"


def test_prepare_sandbox_invalid_when_entry_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    ws_key = "proj_bad_entry"
    writer = WorkspaceLayoutWriter(workspace_key=ws_key)
    writer.ensure_dirs()
    buf = BytesIO()
    manifest = {
        "format": "mcp.package",
        "format_version": 1,
        "name": "bad_entry",
        "version": "1.0.0",
        "runtime": "python3.12",
        "entry": {"command": "python", "args": ["-m", "src.missing"]},
        "tools": [{"name": "x.y", "description": "x"}],
        "permissions": {"cabinet_data": ["read"], "network_hosts": [], "shell": False},
    }
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("mcp.json", json.dumps({"name": "bad_entry", "tools": []}))
        zf.writestr("src/server.py", "pass\n")
    writer.extract_packages([("bad_entry", buf.getvalue())])

    record = prepare_package_sandbox(workspace_root=writer.workspace_root, package_name="bad_entry")
    assert record["status"] == "invalid"
    assert "not found" in record.get("reason", "")


def test_local_spawn_and_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "mcp_sandbox_spawn", True)
    writer = WorkspaceLayoutWriter(workspace_key="proj_spawn")
    writer.ensure_dirs()
    raw = build_minimal_package_zip(name="demo_sync", version="1.0.0")
    writer.extract_packages([("demo_sync", raw)])

    record = prepare_package_sandbox(workspace_root=writer.workspace_root, package_name="demo_sync")
    assert record["status"] == "ready"
    assert record["process"] == "running"
    pid = record["pid"]
    assert isinstance(pid, int) and _pid_alive(pid)

    stopped = stop_package_process(workspace_root=writer.workspace_root, package_name="demo_sync")
    assert stopped["process"] == "stopped"
    assert not _pid_alive(pid)


def test_start_skipped_when_spawn_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "mcp_sandbox_spawn", False)
    writer = WorkspaceLayoutWriter(workspace_key="proj_nospawn")
    writer.ensure_dirs()
    raw = build_minimal_package_zip(name="demo_sync", version="1.0.0")
    writer.extract_packages([("demo_sync", raw)])
    prepare_package_sandbox(workspace_root=writer.workspace_root, package_name="demo_sync")
    again = start_package_process(workspace_root=writer.workspace_root, package_name="demo_sync")
    assert again["process"] == "not_started"
    assert again.get("spawn_skipped")
