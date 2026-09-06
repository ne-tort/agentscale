"""Shared denylist / path rules for workspace dehydrate (pod→object store)."""

from __future__ import annotations

from pathlib import PurePosixPath

# Top-level or nested junk that must not round-trip into MinIO last-good.
_EXCLUDED_NAME_PARTS = frozenset(
    {
        "node_modules",
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)

_MAX_FILE_BYTES = 32 * 1024 * 1024  # 32 MiB per file safety cap


def is_excluded_rel(rel: str) -> bool:
    """Return True if relative path under /workspace should not be uploaded."""
    path = PurePosixPath(rel.replace("\\", "/").lstrip("/"))
    if not path.parts or path.parts[0] == "..":
        return True
    for part in path.parts:
        if part in _EXCLUDED_NAME_PARTS:
            return True
        if part.endswith(".pyc"):
            return True
    return False


def tar_exclude_args() -> list[str]:
    """GNU/busybox tar --exclude args for dehydrate stream."""
    args: list[str] = []
    for name in sorted(_EXCLUDED_NAME_PARTS):
        args.extend(["--exclude", f"./{name}"])
        args.extend(["--exclude", f"*/{name}"])
        args.extend(["--exclude", f"*/{name}/*"])
    args.extend(["--exclude", "*.pyc"])
    return args


def max_file_bytes() -> int:
    return _MAX_FILE_BYTES
