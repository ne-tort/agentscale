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


def coerce_internal_workspace_path(raw: str | None) -> str:
    """Normalize a path the backend produced itself, not user input.

    ``normalize_workspace_path`` is the strict validator for *user* input: a
    leading ``/`` there is an attempt to address something outside the
    workspace and must be a 422. Adapters and planners, however, receive paths
    from their own code (plan rows, persisted columns, agent output), where a
    leading separator or a Windows-style ``\\`` is a formatting artefact, not an
    escape attempt. Strip those first, then run the same strict normalizer so
    traversal (``..``) and encoded escape attempts are still refused.
    """
    text = (raw or "").strip().replace("\\", "/").lstrip("/")
    return normalize_workspace_path(text)


def workspace_abs_path(relative: str) -> str:
    """Absolute POSIX path inside pod for a validated relative path."""
    rel = normalize_workspace_path(relative)
    if not rel:
        return str(_WORKSPACE_ROOT)
    return str(_WORKSPACE_ROOT / rel)
