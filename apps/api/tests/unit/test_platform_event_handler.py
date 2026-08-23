"""Unit tests — platform event handler invoke from zip."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

from prodavan.infrastructure.cabinets.package_codec import build_minimal_package_zip
from prodavan.infrastructure.cabinets.platform_event_handler import (
    _parse_stdout_result,
    invoke_platform_event_from_artifact,
)


def test_invoke_runs_handler_script(tmp_path: Path) -> None:
    raw = build_minimal_package_zip(
        name="evt_hook",
        platform_events=["company.suspended"],
        with_platform_event_handler=True,
    )
    artifact = tmp_path / "evt_hook-1.0.0.zip"
    artifact.write_bytes(raw)

    result = invoke_platform_event_from_artifact(
        artifact_path=artifact,
        package_name="evt_hook",
        event={"platform_event_type": "company.suspended"},
    )
    assert result["action"] == "invoked"
    assert result["exit_code"] == 0
    assert result.get("result", {}).get("ok") is True
    assert result["result"]["event_type"] == "company.suspended"


def test_invoke_stub_when_no_script(tmp_path: Path) -> None:
    raw = build_minimal_package_zip(name="no_hook")
    artifact = tmp_path / "no_hook-1.0.0.zip"
    artifact.write_bytes(raw)

    result = invoke_platform_event_from_artifact(
        artifact_path=artifact,
        package_name="no_hook",
        event={"platform_event_type": "company.suspended"},
    )
    assert result["action"] == "stub"
    assert result["reason"] == "no handler script"


def test_parse_stdout_result_last_json_line() -> None:
    assert _parse_stdout_result('log line\n{"ok": true, "n": 1}\n') == {"ok": True, "n": 1}
    assert _parse_stdout_result("not json") is None


def test_invoke_failed_on_nonzero_exit(tmp_path: Path) -> None:
    buf = BytesIO()
    manifest = {
        "format": "mcp.package",
        "format_version": 1,
        "name": "bad_hook",
        "version": "1.0.0",
        "runtime": "python3.12",
        "entry": {"command": "python", "args": ["-m", "src.server"]},
        "tools": [{"name": "bad.ping", "description": "x"}],
        "permissions": {"cabinet_data": ["read"], "network_hosts": [], "shell": False},
    }
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("mcp.json", json.dumps({"name": "bad_hook"}))
        zf.writestr("src/__init__.py", "")
        zf.writestr("src/server.py", "pass\n")
        zf.writestr("src/on_platform_event.py", "import sys\nsys.exit(2)\n")
    artifact = tmp_path / "bad_hook-1.0.0.zip"
    artifact.write_bytes(buf.getvalue())

    result = invoke_platform_event_from_artifact(
        artifact_path=artifact,
        package_name="bad_hook",
        event={"platform_event_type": "x"},
    )
    assert result["action"] == "failed"
    assert result["exit_code"] == 2
