"""Unit tests — workspace_fs exec command builder."""

from __future__ import annotations

from prodavan.application.pod_service.adapters.k8s.workspace_exec_cmd import build_workspace_fs_command


def test_build_workspace_fs_command_uses_module_entrypoint() -> None:
    cmd = build_workspace_fs_command(["list", ""])
    assert cmd == ["python", "-m", "prodavan.runtime.workspace_fs", "list", ""]
