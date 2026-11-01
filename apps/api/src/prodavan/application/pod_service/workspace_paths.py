"""Safe relative paths inside Pod /workspace."""

from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import unquote

from prodavan.domain.errors import AppError

_WORKSPACE_ROOT = PurePosixPath("/workspace")


def normalize_workspace_path(raw: str | None) -> str:
    """Return normalized relative path (no leading slash) under workspace root."""
    text = (raw or "").strip()
    # Decode repeatedly so HTTP-encoded separators and traversal cannot survive
    # one normalization pass (for example ``%252e%252e%252fsecret``).
    for _ in range(3):
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    text = text.replace("\\", "/")
    if text in ("", "/"):
        return ""
    if text.startswith("/"):
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="path must be relative to workspace root",
        )
    raw_parts = text.split("/")
    if ".." in raw_parts:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="path must not contain ..",
        )
    parts = PurePosixPath(text).parts
    if not parts or parts == (".",):
        return ""
    return str(PurePosixPath(*parts))


def workspace_abs_path(relative: str) -> str:
    """Absolute POSIX path inside pod for a validated relative path."""
    rel = normalize_workspace_path(relative)
    if not rel:
        return str(_WORKSPACE_ROOT)
    return str(_WORKSPACE_ROOT / rel)
