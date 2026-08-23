"""MCP package zip validation (mcp.package-v1) — no DB I/O."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from typing import Any

from prodavan.domain.errors import AppError

FORMAT_NAME = "mcp.package"
FORMAT_VERSION = 1
MAX_PACKAGE_BYTES = 5 * 1024 * 1024
ALLOWED_RUNTIMES = frozenset({"python3.12"})
ALLOWED_ENTRY_COMMANDS = frozenset({"python"})
ALLOWED_PATH_PREFIXES = ("manifest.json", "mcp.json", "src/", "requirements.txt", "README.md")


def validate_package_zip(raw: bytes) -> dict[str, Any]:
    """Strict validate mcp.package-v1.zip → manifest + meta for registry."""
    if not raw:
        raise AppError(code="PACKAGE_INVALID", title="Invalid package", status=422, detail="empty zip")
    if len(raw) > MAX_PACKAGE_BYTES:
        raise AppError(code="PACKAGE_TOO_LARGE", title="Package too large", status=413, detail="package exceeds limit")

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise AppError(code="PACKAGE_INVALID", title="Invalid package", status=422, detail="not a zip") from exc

    names = [n for n in zf.namelist() if not n.endswith("/")]
    for name in names:
        if ".." in name or name.startswith("/") or "\\" in name:
            raise AppError(code="PACKAGE_INVALID", title="Invalid package", status=422, detail="unsafe path")
        ok = name in {"manifest.json", "mcp.json", "requirements.txt", "README.md"} or name.startswith("src/")
        if not ok:
            raise AppError(
                code="PACKAGE_INVALID",
                title="Invalid package",
                status=422,
                detail=f"forbidden path: {name}",
            )

    if "manifest.json" not in names:
        raise AppError(code="PACKAGE_INVALID", title="Invalid package", status=422, detail="manifest.json missing")
    if "mcp.json" not in names:
        raise AppError(code="PACKAGE_INVALID", title="Invalid package", status=422, detail="mcp.json missing")

    try:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppError(code="PACKAGE_INVALID", title="Invalid package", status=422, detail="bad manifest") from exc

    if manifest.get("format") != FORMAT_NAME or int(manifest.get("format_version") or 0) != FORMAT_VERSION:
        raise AppError(code="PACKAGE_INVALID", title="Invalid package", status=422, detail="unsupported format")

    name = str(manifest.get("name") or "").strip()
    version = str(manifest.get("version") or "").strip()
    if not name.replace("_", "").replace("-", "").isalnum() or not name.islower():
        raise AppError(code="PACKAGE_INVALID", title="Invalid package", status=422, detail="bad package name")
    if not version or len(version) > 32:
        raise AppError(code="PACKAGE_INVALID", title="Invalid package", status=422, detail="bad version")

    runtime = str(manifest.get("runtime") or "")
    if runtime not in ALLOWED_RUNTIMES:
        raise AppError(code="PACKAGE_INVALID", title="Invalid package", status=422, detail="runtime not allowlisted")

    entry = manifest.get("entry") or {}
    if not isinstance(entry, dict):
        raise AppError(code="PACKAGE_INVALID", title="Invalid package", status=422, detail="bad entry")
    cmd = str(entry.get("command") or "")
    if cmd not in ALLOWED_ENTRY_COMMANDS:
        raise AppError(
            code="PACKAGE_INVALID",
            title="Invalid package",
            status=422,
            detail="entry.command not allowlisted",
        )
    args = entry.get("args")
    if args is not None and not isinstance(args, list):
        raise AppError(code="PACKAGE_INVALID", title="Invalid package", status=422, detail="entry.args must be list")

    perms = manifest.get("permissions") or {}
    if not isinstance(perms, dict):
        raise AppError(code="PACKAGE_INVALID", title="Invalid package", status=422, detail="bad permissions")
    if perms.get("shell") is not False:
        raise AppError(
            code="PACKAGE_INVALID",
            title="Invalid package",
            status=422,
            detail="permissions.shell must be false",
        )
    hosts = perms.get("network_hosts") or []
    if not isinstance(hosts, list):
        raise AppError(code="PACKAGE_INVALID", title="Invalid package", status=422, detail="bad network_hosts")

    tools = manifest.get("tools")
    if not isinstance(tools, list) or not tools:
        raise AppError(code="PACKAGE_INVALID", title="Invalid package", status=422, detail="tools required")

    platform_events = manifest.get("platform_events")
    if platform_events is None:
        platform_events = manifest.get("on_platform_event")
    if platform_events is not None:
        if isinstance(platform_events, str):
            platform_events = [platform_events]
        if not isinstance(platform_events, list) or not all(
            isinstance(x, str) and x.strip() for x in platform_events
        ):
            raise AppError(
                code="PACKAGE_INVALID",
                title="Invalid package",
                status=422,
                detail="platform_events must be a list of non-empty strings",
            )

    try:
        mcp_json = json.loads(zf.read("mcp.json").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppError(code="PACKAGE_INVALID", title="Invalid package", status=422, detail="bad mcp.json") from exc
    if not isinstance(mcp_json, dict):
        raise AppError(code="PACKAGE_INVALID", title="Invalid package", status=422, detail="mcp.json must be object")

    content_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
    # manifest.content_hash is informational; registry always stores hash of deployed bytes

    return {
        "manifest": manifest,
        "mcp_json": mcp_json,
        "name": name,
        "version": version,
        "runtime": runtime,
        "content_hash": content_hash,
        "size_bytes": len(raw),
        "tool_names": [str(t.get("name")) for t in tools if isinstance(t, dict)],
    }


def build_minimal_package_zip(
    *,
    name: str = "demo_sync",
    version: str = "1.0.0",
    tool_name: str = "demo.ping",
    platform_events: list[str] | None = None,
    with_platform_event_handler: bool = False,
) -> bytes:
    """Test helper — valid minimal package."""
    manifest = {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "name": name,
        "version": version,
        "runtime": "python3.12",
        "entry": {"command": "python", "args": ["-m", "src.server"]},
        "tools": [{"name": tool_name, "description": "ping"}],
        "permissions": {"cabinet_data": ["read", "write"], "network_hosts": [], "shell": False},
    }
    if platform_events:
        manifest["platform_events"] = platform_events
    mcp_json = {"name": name, "tools": [{"name": tool_name}]}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("mcp.json", json.dumps(mcp_json, indent=2))
        zf.writestr("src/__init__.py", "")
        if with_platform_event_handler:
            zf.writestr(
                "src/on_platform_event.py",
                (
                    "import json\n"
                    "import sys\n\n"
                    "if __name__ == '__main__':\n"
                    "    event = json.load(sys.stdin)\n"
                    "    print(json.dumps({'ok': True, 'event_type': event.get('platform_event_type')}))\n"
                    "    sys.exit(0)\n"
                ),
            )
        zf.writestr(
            "src/server.py",
            (
                "import time\n\n"
                "def main():\n"
                "    while True:\n"
                "        time.sleep(3600)\n\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
        )
    return buf.getvalue()
