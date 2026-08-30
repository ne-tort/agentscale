"""Build in-pod workspace_fs exec argv."""

from __future__ import annotations


def build_workspace_fs_command(args: list[str]) -> list[str]:
    """Return argv to run workspace_fs inside the sandbox container."""
    return ["python", "-m", "prodavan.runtime.workspace_fs", *args]
