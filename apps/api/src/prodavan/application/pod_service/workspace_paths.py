"""Safe relative paths inside Pod /workspace."""

from __future__ import annotations

from pathlib import PurePosixPath

from prodavan.domain.errors import AppError

_WORKSPACE_ROOT = PurePosixPath("/workspace")


def normalize_workspace_path(raw: str | None) -> str:
    """Return normalized relative path (no leading slash) under workspace root."""
    text = (raw or "").strip().replace("\\", "/")
    if text in ("", "/"):
        return ""
    if text.startswith("/"):
        text = text.lstrip("/")
    parts = PurePosixPath(text).parts
    if ".." in parts:
        raise AppError(
            code="VALIDATION_ERROR",
            title="Validation Error",
            status=422,
            detail="path must not contain ..",
        )
    return str(PurePosixPath(*parts)) if parts else ""


def workspace_abs_path(relative: str) -> str:
    """Absolute POSIX path inside pod for a validated relative path."""
    rel = normalize_workspace_path(relative)
    if not rel:
        return str(_WORKSPACE_ROOT)
    return str(_WORKSPACE_ROOT / rel)
