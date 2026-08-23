"""Cabinet bundle zip codec (format v1) — no I/O beyond bytes."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from typing import Any

from prodavan.domain.errors import AppError

FORMAT_NAME = "cabinet.bundle"
FORMAT_VERSION = 1
MAX_BUNDLE_BYTES = 25 * 1024 * 1024
ALLOWED_ROOT_PREFIXES = ("manifest.json", "meta/", "data/", "mcp_packages/", "README.md")


def pack_bundle(
    *,
    name: str,
    exported_from_cabinet_id: str,
    tables: list[dict],
    columns: list[dict],
    tabs: list[dict],
    views: list[dict],
    mcp_tools: list[dict] | None = None,
    data_by_slug: dict[str, list[dict]] | None = None,
    package_files: dict[str, bytes] | None = None,
    base_version: str = "1.0.0",
) -> bytes:
    """Build cabinet.bundle-v1.zip bytes. Secrets must already be stripped by caller."""
    meta_files = {
        "meta/tables.json": tables,
        "meta/columns.json": columns,
        "meta/tabs.json": tabs,
        "meta/views.json": views,
        "meta/mcp_tools.json": mcp_tools or [],
    }
    # Hash payload without manifest (manifest embeds the hash)
    hasher = hashlib.sha256()
    for path in sorted(meta_files):
        blob = json.dumps(meta_files[path], ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        hasher.update(path.encode("utf-8"))
        hasher.update(blob)
    data_by_slug = data_by_slug or {}
    for slug in sorted(data_by_slug):
        lines = "\n".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) for row in data_by_slug[slug]
        )
        hasher.update(f"data/{slug}.jsonl".encode())
        hasher.update(lines.encode("utf-8"))
    package_files = package_files or {}
    for fname in sorted(package_files):
        hasher.update(f"mcp_packages/{fname}".encode())
        hasher.update(package_files[fname])

    manifest = {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "name": name,
        "base_version": base_version,
        "created_at": datetime.now(UTC).isoformat(),
        "exported_from_cabinet_id": exported_from_cabinet_id,
        "content_hash": "sha256:" + hasher.hexdigest(),
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for path, payload in meta_files.items():
            zf.writestr(path, json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        for slug, rows in data_by_slug.items():
            if not slug.replace("_", "").isalnum() or not slug.islower():
                raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad data slug")
            body = "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True, default=str) for r in rows)
            if body:
                body += "\n"
            zf.writestr(f"data/{slug}.jsonl", body)
        for fname, raw in package_files.items():
            safe = fname.replace("\\", "/").split("/")[-1]
            if not safe.endswith(".zip") or ".." in safe:
                raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad package name")
            zf.writestr(f"mcp_packages/{safe}", raw)
        zf.writestr("README.md", f"# {name}\n\nProdavan cabinet.bundle v{FORMAT_VERSION}\n")
    out = buf.getvalue()
    if len(out) > MAX_BUNDLE_BYTES:
        raise AppError(code="BUNDLE_TOO_LARGE", title="Bundle too large", status=413, detail="bundle exceeds limit")
    return out


def unpack_bundle(raw: bytes) -> dict[str, Any]:
    """Validate and parse bundle zip → structured dict."""
    if not raw:
        raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="empty bundle")
    if len(raw) > MAX_BUNDLE_BYTES:
        raise AppError(code="BUNDLE_TOO_LARGE", title="Bundle too large", status=413, detail="bundle exceeds limit")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise AppError(code="BUNDLE_INVALID", title="Invalid bundle", status=422, detail="not a zip") from exc

    names = zf.namelist()
    for name in names:
        if name.endswith("/"):
            continue
        if ".." in name or name.startswith("/") or "\\" in name:
            raise AppError(code="BUNDLE_INVALID", title="Invalid bundle", status=422, detail="unsafe path")
        if not any(name == p or name.startswith(p) for p in ALLOWED_ROOT_PREFIXES if p.endswith("/")) and name not in {
            "manifest.json",
            "README.md",
        }:
            if not (
                name.startswith("meta/")
                or name.startswith("data/")
                or name.startswith("mcp_packages/")
                or name in {"manifest.json", "README.md"}
            ):
                raise AppError(
                    code="BUNDLE_INVALID",
                    title="Invalid bundle",
                    status=422,
                    detail=f"forbidden path: {name}",
                )
        # Loose binaries outside packages forbidden
        if name.startswith("mcp_packages/") and not name.endswith(".zip"):
            if not name.endswith("/"):
                raise AppError(
                    code="BUNDLE_INVALID",
                    title="Invalid bundle",
                    status=422,
                    detail="mcp_packages must contain only .zip",
                )

    if "manifest.json" not in names:
        raise AppError(code="BUNDLE_INVALID", title="Invalid bundle", status=422, detail="manifest.json missing")

    try:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppError(code="BUNDLE_INVALID", title="Invalid bundle", status=422, detail="bad manifest") from exc

    if manifest.get("format") != FORMAT_NAME or int(manifest.get("format_version") or 0) != FORMAT_VERSION:
        raise AppError(code="BUNDLE_INVALID", title="Invalid bundle", status=422, detail="unsupported format")

    def _load_json(path: str, default: Any) -> Any:
        if path not in names:
            return default
        try:
            return json.loads(zf.read(path).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError(code="BUNDLE_INVALID", title="Invalid bundle", status=422, detail=f"bad {path}") from exc

    tables = _load_json("meta/tables.json", [])
    columns = _load_json("meta/columns.json", [])
    tabs = _load_json("meta/tabs.json", [])
    views = _load_json("meta/views.json", [])
    mcp_tools = _load_json("meta/mcp_tools.json", [])
    if not isinstance(tables, list) or not isinstance(columns, list):
        raise AppError(code="BUNDLE_INVALID", title="Invalid bundle", status=422, detail="meta must be arrays")

    data_by_slug: dict[str, list[dict]] = {}
    for name in names:
        if not name.startswith("data/") or not name.endswith(".jsonl"):
            continue
        slug = name.removeprefix("data/").removesuffix(".jsonl")
        if not slug.replace("_", "").isalnum() or not slug.islower():
            raise AppError(code="BUNDLE_INVALID", title="Invalid bundle", status=422, detail=f"bad data file {name}")
        text = zf.read(name).decode("utf-8")
        rows: list[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AppError(code="BUNDLE_INVALID", title="Invalid bundle", status=422, detail="bad jsonl") from exc
            if not isinstance(obj, dict):
                raise AppError(
                    code="BUNDLE_INVALID",
                    title="Invalid bundle",
                    status=422,
                    detail="jsonl row must be object",
                )
            rows.append(obj)
        data_by_slug[slug] = rows

    # packages listed but not applied yet (Gap: deploy)
    packages = [n for n in names if n.startswith("mcp_packages/") and n.endswith(".zip")]
    package_files: dict[str, bytes] = {}
    for path in packages:
        fname = path.removeprefix("mcp_packages/")
        package_files[fname] = zf.read(path)

    return {
        "manifest": manifest,
        "tables": tables,
        "columns": columns,
        "tabs": tabs,
        "views": views,
        "mcp_tools": mcp_tools,
        "data_by_slug": data_by_slug,
        "mcp_package_paths": packages,
        "package_files": package_files,
    }
