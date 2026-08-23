"""MCP package codec unit tests (L06)."""

from __future__ import annotations

import base64
import io
import json
import zipfile

import pytest

from prodavan.domain.errors import AppError
from prodavan.infrastructure.cabinets.package_codec import (
    build_minimal_package_zip,
    validate_package_zip,
)


def test_valid_minimal_package() -> None:
    raw = build_minimal_package_zip()
    meta = validate_package_zip(raw)
    assert meta["name"] == "demo_sync"
    assert meta["runtime"] == "python3.12"
    assert "demo.ping" in meta["tool_names"]
    assert meta["content_hash"].startswith("sha256:")


def test_rejects_shell_true() -> None:
    raw = build_minimal_package_zip()
    zf = zipfile.ZipFile(io.BytesIO(raw))
    manifest = json.loads(zf.read("manifest.json"))
    manifest["permissions"]["shell"] = True
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        out.writestr("manifest.json", json.dumps(manifest))
        out.writestr("mcp.json", zf.read("mcp.json"))
        out.writestr("src/server.py", b"x")
    with pytest.raises(AppError) as ei:
        validate_package_zip(buf.getvalue())
    assert ei.value.code == "PACKAGE_INVALID"
    assert "shell" in (ei.value.detail or "")


def test_rejects_unknown_runtime() -> None:
    raw = build_minimal_package_zip()
    zf = zipfile.ZipFile(io.BytesIO(raw))
    manifest = json.loads(zf.read("manifest.json"))
    manifest["runtime"] = "node20"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        out.writestr("manifest.json", json.dumps(manifest))
        out.writestr("mcp.json", zf.read("mcp.json"))
    with pytest.raises(AppError) as ei:
        validate_package_zip(buf.getvalue())
    assert "runtime" in (ei.value.detail or "")


def test_rejects_path_traversal() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": "mcp.package",
                    "format_version": 1,
                    "name": "x",
                    "version": "1",
                    "runtime": "python3.12",
                    "entry": {"command": "python", "args": []},
                    "tools": [{"name": "t"}],
                    "permissions": {"shell": False},
                }
            ),
        )
        zf.writestr("mcp.json", "{}")
        zf.writestr("../evil.py", "x")
    with pytest.raises(AppError):
        validate_package_zip(buf.getvalue())


def test_base64_roundtrip_helper() -> None:
    raw = build_minimal_package_zip(name="suppliers_sync")
    b64 = base64.b64encode(raw).decode()
    assert validate_package_zip(base64.b64decode(b64))["name"] == "suppliers_sync"
