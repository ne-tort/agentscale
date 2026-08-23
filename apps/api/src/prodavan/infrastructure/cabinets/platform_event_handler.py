"""Invoke MCP package platform event handlers from deployed zip artifacts (L06/L07).

Stdio contract (lite — not full MCP stdio):
- stdin: one JSON object (UTF-8) with platform_event_id / platform_event_type / …
- stdout: optional one JSON object; parsed into ``result`` when valid
- exit 0 → action=invoked; non-zero → action=failed
"""

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
_STDOUT_CAP = 4000


def _parse_stdout_result(stdout: str) -> dict[str, Any] | None:
    text = stdout.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Prefer last non-empty line (handlers may log then print JSON).
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        else:
            return None
    return parsed if isinstance(parsed, dict) else None


def invoke_platform_event_from_bytes(
    *,
    zip_bytes: bytes,
    package_name: str,
    event: dict[str, Any],
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Run handler from in-memory zip (object-store SoT without local mirror)."""
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(zip_bytes)
        path = Path(tmp.name)
    try:
        return invoke_platform_event_from_artifact(
            artifact_path=path,
            package_name=package_name,
            event=event,
            timeout_sec=timeout_sec,
        )
    finally:
        path.unlink(missing_ok=True)


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

        stdout = (proc.stdout or b"").decode("utf-8", errors="replace")[:_STDOUT_CAP]
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace")[:500]
        out: dict[str, Any] = {
            "action": "invoked" if proc.returncode == 0 else "failed",
            "package": package_name,
            "exit_code": proc.returncode,
            "stdout": stdout[:500],
            "stderr": stderr,
        }
        parsed = _parse_stdout_result(stdout)
        if parsed is not None:
            out["result"] = parsed
        return out
