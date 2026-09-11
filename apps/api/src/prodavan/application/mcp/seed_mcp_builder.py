"""Build platform seed MCP zips from seed_mcp_packages/ + application sources."""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

# apps/api/
_API_ROOT = Path(__file__).resolve().parents[4]
_SEED_ROOT = _API_ROOT / "seed_mcp_packages"
_SRC_ROOT = _API_ROOT / "src" / "prodavan"


@dataclass(frozen=True, slots=True)
class SeedPackageSpec:
    """One replaceable platform MCP package."""

    name: str
    # Relative to seed_mcp_packages/
    manifest_dir: str
    # (zip_arcname, absolute source path)
    source_files: tuple[tuple[str, Path], ...]


def _equipment_sources() -> tuple[tuple[str, Path], ...]:
    return (
        (
            "server.py",
            _SRC_ROOT / "application" / "mcp" / "prodavan_equipment_mcp" / "server.py",
        ),
        (
            "equipment_catalog_search.py",
            _SRC_ROOT / "application" / "modules" / "equipment_catalog_search.py",
        ),
    )


SEED_PACKAGES: tuple[SeedPackageSpec, ...] = (
    SeedPackageSpec(
        name="prodavan-equipment",
        manifest_dir="prodavan-equipment",
        source_files=_equipment_sources(),
    ),
)


def seed_mcp_root() -> Path:
    return _SEED_ROOT


def load_manifest(spec: SeedPackageSpec) -> dict[str, Any]:
    path = _SEED_ROOT / spec.manifest_dir / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid manifest: {path}")
    if data.get("format") != "mcp.package":
        raise ValueError(f"manifest.format must be mcp.package: {path}")
    if str(data.get("name") or "") != spec.name:
        raise ValueError(f"manifest.name must be {spec.name}: {path}")
    return data


def build_seed_mcp_zip(spec: SeedPackageSpec) -> tuple[bytes, dict[str, Any]]:
    """Return (zip_bytes, manifest). Zip root = extract dir packages/{name}/."""
    manifest = load_manifest(spec)
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for arcname, src in spec.source_files:
            if not src.is_file():
                raise FileNotFoundError(f"seed MCP source missing: {src}")
            zf.writestr(arcname, src.read_text(encoding="utf-8"))
    return buf.getvalue(), manifest


def storage_key_for(spec: SeedPackageSpec, *, version: str) -> str:
    safe_ver = version.replace("/", "_").replace("\\", "_")
    return f"platform/seed-mcp/{spec.name}-{safe_ver}.zip"
