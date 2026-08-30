"""Unit tests — embedded workspace_fs exec command builder."""

from __future__ import annotations

from prodavan.application.pod_service.adapters.k8s.workspace_exec_cmd import build_workspace_fs_command


def test_build_workspace_fs_command_uses_embedded_source() -> None:
    cmd = build_workspace_fs_command(["list", ""])
    assert cmd[:2] == ["python", "-c"]
    body = cmd[2]
    assert "base64.b64decode" in body
    assert "ns['main']" in body
    assert "list" in body
