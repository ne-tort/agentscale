"""Validate mcp.package-v1.zip format."""

from __future__ import annotations

import hashlib
import json
import zipfile
from io import BytesIO
from typing import Any

from prodavan.domain.errors import AppError

_MAX_ZIP_BYTES = 20 * 1024 * 1024


class McpPackageValidator:
    def validate_zip(self, raw: bytes) -> dict[str, Any]:
        if len(raw) > _MAX_ZIP_BYTES:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="mcp package zip too large",
            )
        try:
            with zipfile.ZipFile(BytesIO(raw)) as zf:
                if "manifest.json" not in zf.namelist():
                    raise AppError(
                        code="VALIDATION_ERROR",
                        title="Validation Error",
                        status=422,
                        detail="manifest.json required",
                    )
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="invalid mcp package zip",
            ) from exc
        if manifest.get("format") != "mcp.package":
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="manifest.format must be mcp.package",
            )
        name = manifest.get("name")
        if not isinstance(name, str) or not name.strip():
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="manifest.name required",
            )
        digest = hashlib.sha256(raw).hexdigest()
        manifest = dict(manifest)
        manifest["_content_hash"] = f"sha256:{digest}"
        return manifest
