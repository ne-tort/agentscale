"""Safe relative paths for workspace tar members (hydrate / dehydrate)."""

from __future__ import annotations


def tar_member_relpath(name: str) -> str | None:
    """Normalize a tar member name to a workspace-relative path.

    Important: do **not** use ``str.lstrip("./")`` — that strips any leading
    ``.`` / ``/`` characters and corrupts hidden dirs like ``.prodavan/``.
    """
    rel = (name or "").replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    rel = rel.lstrip("/")
    if not rel or rel in {".", ".."}:
        return None
    parts = rel.split("/")
    if any(p == ".." for p in parts):
        return None
    return rel
