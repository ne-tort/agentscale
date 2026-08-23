"""Invoke MCP package platform event handlers from deployed zip artifacts (L06/L07)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

_DEFAULT_TIMEOUT_SEC = 5.0
_HANDLER_REL = Path("src") / "on_platform_event.py"


def invoke_platform_event_from_artifact(
    *,
    artifact_path: Path,
    package_name: str,
    event: dict[str, Any],
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Run `src/on_platform_event.py` from package zip with event JSON on stdin."""
    if not artifact_path.is_file():
        return {"action": "failed", "reason": "artifact missing", "package": package_name}

    payload = json.dumps(event, ensure_ascii=False).encode("utf-8")

    with tempfile.TemporaryDirectory(prefix="prodavan-pevt-") as tmp:
        try:
            with zipfile.ZipFile(artifact_path) as zf:
                zf.extractall(tmp)
        except (OSError, zipfile.BadZipFile) as exc:
            return {"action": "failed", "reason": f"extract failed: {exc}", "package": package_name}

        script = Path(tmp) / _HANDLER_REL
        if not script.is_file():
            return {"action": "stub", "reason": "no handler script", "package": package_name}

        try:
            proc = subprocess.run(  # noqa: S603 — python entry from vetted package zip
                [sys.executable, str(script)],
                input=payload,
                cwd=tmp,
                capture_output=True,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired:
            return {
                "action": "failed",
                "reason": "handler timeout",
                "package": package_name,
                "timeout_sec": timeout_sec,
            }
        except OSError as exc:
            return {"action": "failed", "reason": str(exc), "package": package_name}

        stdout = (proc.stdout or b"").decode("utf-8", errors="replace")[:500]
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace")[:500]
        return {
            "action": "invoked" if proc.returncode == 0 else "failed",
            "package": package_name,
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
