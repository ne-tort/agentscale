"""Unit tests for MCP package validator."""

import json
import zipfile
from io import BytesIO

import pytest

from prodavan.application.mcp.package_validator import McpPackageValidator
from prodavan.domain.errors import AppError


def _zip_with_manifest(name: str = "demo") -> bytes:
    buf = BytesIO()
    manifest = {
        "format": "mcp.package",
        "format_version": 1,
        "name": name,
        "version": "1.0.0",
        "entry": {"command": "python", "args": ["-m", "srv"]},
        "tools": [],
    }
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
    return buf.getvalue()


def test_validate_zip_accepts_valid_package() -> None:
    out = McpPackageValidator().validate_zip(_zip_with_manifest())
    assert out["name"] == "demo"
    assert out["_content_hash"].startswith("sha256:")


def test_validate_zip_rejects_missing_manifest() -> None:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hi")
    with pytest.raises(AppError):
        McpPackageValidator().validate_zip(buf.getvalue())
