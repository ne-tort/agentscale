from __future__ import annotations

import subprocess

import pytest

from prodavan_ops import heal


def _proc(stdout: str = "", stderr: str = "", code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["kubectl"], returncode=code, stdout=stdout, stderr=stderr)


_TWO_INGRESSES = """apiVersion: v1
kind: Namespace
metadata:
  name: agentscale-dev
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: agentscale
spec:
  rules: []
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: agentscale-https
spec:
  rules: []
"""


def test_reconcile_applies_only_ingress_documents(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """The overlay also renders a Namespace and ConfigMaps; only Ingress matters here.

    Re-applying a ConfigMap would overwrite values Argo may not have synced yet,
    which is not this function's job.
    """
    seen: list[dict] = []

    def fake_kubectl(args, **kwargs):
        seen.append({"args": list(args), "stdin": kwargs.get("stdin_text")})
        if args[0] == "kustomize":
            return _proc(stdout=_TWO_INGRESSES)
        return _proc(stdout="ingress.networking.k8s.io/agentscale configured")

    monkeypatch.setattr(heal, "kubectl", fake_kubectl)
    monkeypatch.setattr(heal, "overlay_dev", lambda: tmp_path)

    heal.reconcile_ingress_route()

    applied = [call for call in seen if call["args"][0] == "apply"]
    assert len(applied) == 1
    payload = applied[0]["stdin"] or ""
    assert payload.count("kind: Ingress") == 2
    assert "kind: Namespace" not in payload
    assert "kind: ConfigMap" not in payload
    assert applied[0]["args"] == ["apply", "-f", "-"]


def test_reconcile_gives_up_when_the_render_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []

    def fake_kubectl(args, **kwargs):
        calls.append(args[0])
        return _proc(stderr="error: not a valid directory", code=1)

    monkeypatch.setattr(heal, "kubectl", fake_kubectl)
    monkeypatch.setattr(heal, "overlay_dev", lambda: tmp_path)

    heal.reconcile_ingress_route()

    assert "apply" not in calls, "must not apply anything it could not render"
    assert "reconcile skipped" in capsys.readouterr().out


def test_reconcile_skips_when_the_overlay_has_no_ingress(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_kubectl(args, **kwargs):
        return _proc(stdout="apiVersion: v1\nkind: Namespace\nmetadata:\n  name: x\n")

    monkeypatch.setattr(heal, "kubectl", fake_kubectl)
    monkeypatch.setattr(heal, "overlay_dev", lambda: tmp_path)

    heal.reconcile_ingress_route()

    assert "no Ingress" in capsys.readouterr().out


def test_reconcile_reports_a_failed_apply(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_kubectl(args, **kwargs):
        if args[0] == "kustomize":
            return _proc(stdout=_TWO_INGRESSES)
        return _proc(stderr="error: forbidden", code=1)

    monkeypatch.setattr(heal, "kubectl", fake_kubectl)
    monkeypatch.setattr(heal, "overlay_dev", lambda: tmp_path)

    heal.reconcile_ingress_route()

    assert "reconcile failed: error: forbidden" in capsys.readouterr().out
