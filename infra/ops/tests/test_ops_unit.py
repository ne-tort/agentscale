"""Offline unit tests for prodavan-ops (no cluster required)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from prodavan_ops.k8s import DEPLOYMENT_TARGETS, FIRST_PARTY_DEPLOYMENTS, assert_kubeconfig_docker_ready
from prodavan_ops.validate import (
    FIRST_PARTY_LATEST,
    assert_no_compose_or_k3d,
    assert_no_shell_scripts,
    verify_image_pins,
)


def test_first_party_deployments_cover_api_web_celery() -> None:
    names = set(FIRST_PARTY_DEPLOYMENTS)
    assert "agentscale-api" in names
    assert "agentscale-web" in names
    assert "agentscale-celery-worker" in names
    assert "agentscale-celery-beat" in names


def test_first_party_deployments_cover_indexer() -> None:
    """Регресс: индексатор не участвовал в rollout и неделями жил на старом коде.

    Он крутится на том же образе :latest, но отдельным deployment'ом, поэтому
    без явного включения в цели его правки (например doc_id офферов каталога)
    не доезжали до dev: под оставался старым, пока его не пересоздавали по
    другой причине.
    """
    assert "agentscale-celery-indexer" in set(FIRST_PARTY_DEPLOYMENTS)
    assert ("agentscale-celery-indexer", "agentscale-dev") in set(DEPLOYMENT_TARGETS)


def test_deployment_targets_include_probe_pod_in_sandboxes_namespace() -> None:
    """Probe pod (agent-runtime) lives in agentscale-dev-sandboxes, not agentscale."""
    names_by_ns: dict[str, set[str]] = {}
    for name, ns in DEPLOYMENT_TARGETS:
        names_by_ns.setdefault(ns, set()).add(name)
    assert "agentscale-probe-pod" in names_by_ns.get("agentscale-dev-sandboxes", set())
    # agentscale namespace still has api/web/celery.
    agentscale = names_by_ns.get("agentscale-dev", set())
    assert "agentscale-api" in agentscale
    assert "agentscale-web" in agentscale


def test_deployment_targets_names_match_first_party() -> None:
    """agentscale-namespace targets in DEPLOYMENT_TARGETS == FIRST_PARTY_DEPLOYMENTS."""
    agentscale_targets = {name for name, ns in DEPLOYMENT_TARGETS if ns == "agentscale-dev"}
    assert agentscale_targets == set(FIRST_PARTY_DEPLOYMENTS)


def test_assert_kubeconfig_docker_ready_noop_without_ci_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("PRODAVAN_CI_HOST", raising=False)
    kube = tmp_path / "kube.yaml"
    kube.write_text("clusters:\n- cluster:\n    server: https://127.0.0.1:6443\n", encoding="utf-8")
    monkeypatch.setenv("KUBECONFIG", str(kube))
    assert_kubeconfig_docker_ready()  # must not raise


def test_prepare_docker_kubeconfig_rewrites_server(tmp_path: Path) -> None:
    from prodavan_ops.k8s import prepare_docker_kubeconfig

    src = tmp_path / "in.yaml"
    dest = tmp_path / "out.yaml"
    src.write_text(
        "\ufeffapiVersion: v1\n"
        "clusters:\n"
        "- cluster:\n"
        "    certificate-authority-data: QQ==\n"
        "    server: https://127.0.0.1:6443\n"
        "  name: default\n",
        encoding="utf-8",
    )
    prepare_docker_kubeconfig(src, dest)
    out = dest.read_text(encoding="utf-8")
    assert "host.docker.internal:6443" in out
    assert "127.0.0.1" not in out
    assert "insecure-skip-tls-verify: true" in out
    assert not out.startswith("\ufeff")


def test_resolve_kubeconfig_rewrites_when_ci_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from prodavan_ops.k8s import resolve_kubeconfig_path

    src = tmp_path / "prodavan-dev.yaml"
    src.write_text(
        "apiVersion: v1\n"
        "clusters:\n"
        "- cluster:\n"
        "    server: https://host.docker.internal:6443\n"
        "  name: default\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PRODAVAN_CI_HOST", "host.docker.internal")
    monkeypatch.setenv("KUBECONFIG", str(src))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "rt"))
    path = resolve_kubeconfig_path()
    assert path != src
    text = path.read_text(encoding="utf-8")
    assert "host.docker.internal" in text
    assert "insecure-skip-tls-verify: true" in text
    assert os.environ["KUBECONFIG"] == str(path)


def test_resolve_kubeconfig_skips_rewrite_when_already_docker_yaml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from prodavan_ops import k8s as k8s_mod
    from prodavan_ops.k8s import resolve_kubeconfig_path

    k8s_mod._cached_kubeconfig = None
    dest = tmp_path / "prodavan-kube-docker.yaml"
    dest.write_text(
        "clusters:\n- cluster:\n    server: https://host.docker.internal:6443\n"
        "    insecure-skip-tls-verify: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PRODAVAN_CI_HOST", "host.docker.internal")
    monkeypatch.setenv("KUBECONFIG", str(dest))
    assert resolve_kubeconfig_path() == dest
    k8s_mod._cached_kubeconfig = None


def test_verify_image_pins_accepts_first_party_latest() -> None:
    manifest = """
apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      containers:
        - name: api
          image: ghcr.io/ne-tort/agentscale-api:latest
        - name: web
          image: ghcr.io/ne-tort/agentscale-web:latest
---
# pins
postgres:16.15
redis:7.4.11-alpine
ghcr.io/ne-tort/minio:RELEASE.2024-10-02T17-50-41Z
mongo:7.0.14
opensearchproject/opensearch:2.17.1
curlimages/curl:8.10.1
redpanda:v24.2.4
quay.io/keycloak/keycloak:26.0
"""
    verify_image_pins(manifest)


@pytest.mark.parametrize(
    "opensearch_pin,keycloak_pin",
    [
        ("opensearchproject/opensearch:2.17.1", "quay.io/keycloak/keycloak:26.0"),
        ("ghcr.io/ne-tort/opensearch:2.17.1", "ghcr.io/ne-tort/keycloak:26.0"),
        # Смешанное состояние: зеркала переключаются по одному.
        ("ghcr.io/ne-tort/opensearch:2.17.1", "quay.io/keycloak/keycloak:26.0"),
        ("opensearchproject/opensearch:2.17.1", "ghcr.io/ne-tort/keycloak:26.0"),
    ],
)
def test_verify_image_pins_accepts_mirrored_and_upstream_pins(
    opensearch_pin: str, keycloak_pin: str
) -> None:
    """Пины не должны зависеть от реестра: ghcr-зеркало или upstream (#299)."""
    manifest = f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agentscale-api
spec:
  template:
    spec:
      containers:
        - name: api
          image: ghcr.io/ne-tort/agentscale-api:latest
        - name: web
          image: ghcr.io/ne-tort/agentscale-web:latest
---
# pins
postgres:16.15
redis:7.4.11-alpine
ghcr.io/ne-tort/minio:RELEASE.2024-10-02T17-50-41Z
mongo:7.0.14
{opensearch_pin}
redpanda:v24.2.4
{keycloak_pin}
"""
    verify_image_pins(manifest)


def test_verify_image_pins_rejects_infra_latest() -> None:
    manifest = """
apiVersion: v1
kind: Pod
metadata:
  name: bad
spec:
  containers:
    - name: pg
      image: postgres:latest
    - name: api
      image: ghcr.io/ne-tort/agentscale-api:latest
    - name: web
      image: ghcr.io/ne-tort/agentscale-web:latest
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: pin-snippets
data:
  note: |
    postgres:16.15
    redis:7.4.11-alpine
    ghcr.io/ne-tort/minio:RELEASE.2024-10-02T17-50-41Z
    redpanda:v24.2.4
    quay.io/keycloak/keycloak:26.0
"""
    with pytest.raises(RuntimeError, match="frozen version"):
        verify_image_pins(manifest)


def test_assert_no_shell_scripts_and_no_legacy_paths() -> None:
    # Repo contract — must stay green on every Gate.
    assert_no_shell_scripts()
    assert_no_compose_or_k3d()


def test_gitops_bootstrap_template_escapes_bash_arrays() -> None:
    """Terraform templatefile must use $${KCTL[@]} so bash arrays survive render."""
    tpl = (
        Path(__file__).resolve().parents[2]
        / "terraform"
        / "modules"
        / "k3s-dev-host"
        / "templates"
        / "gitops-bootstrap.sh.tpl"
    )
    text = tpl.read_text(encoding="utf-8")
    assert "gitops-already-healthy" in text
    assert '"$${KCTL[@]}"' in text
    assert "${kubeconfig_path}" in text
    assert "${remote_repo_path}" in text
    # Unescaped bash array would be eaten by templatefile
    assert '"${KCTL[@]}"' not in text.replace('"$${KCTL[@]}"', "")


def test_export_windows_kubeconfig_script_targets_docker_gateway() -> None:
    tpl = (
        Path(__file__).resolve().parents[2]
        / "terraform"
        / "modules"
        / "k3s-dev-host"
        / "templates"
        / "export-windows-kubeconfig.sh.tpl"
    )
    text = tpl.read_text(encoding="utf-8")
    assert "host.docker.internal" in text
    assert "insecure-skip-tls-verify" in text
    assert "WINDOWS_KUBECONFIG" in text


def test_is_api_unreachable_detects_connection_errors() -> None:
    from kubernetes.client.rest import ApiException
    from prodavan_ops.k8s import _is_api_unreachable

    assert _is_api_unreachable(ApiException(status=0, reason="Connection refused"))
    assert _is_api_unreachable(ConnectionError("connection refused"))
    assert not _is_api_unreachable(ApiException(status=403, reason="Forbidden"))


def test_first_party_latest_constants() -> None:
    assert "ghcr.io/ne-tort/agentscale-api:latest" in FIRST_PARTY_LATEST
    assert "ghcr.io/ne-tort/agentscale-web:latest" in FIRST_PARTY_LATEST
    # Platform probe pod ships in the overlay render as :latest (first-party).
    assert "ghcr.io/ne-tort/prodavan-agent-runtime:latest" in FIRST_PARTY_LATEST
    # WAVE17 — searchnet (web-search gateway) is our CI-built :latest image.
    assert "ghcr.io/ne-tort/searchnet:latest" in FIRST_PARTY_LATEST


def test_pod_stuck_detects_terminating_and_failed() -> None:
    from prodavan_ops.heal import _pod_stuck

    assert _pod_stuck({"metadata": {"deletionTimestamp": "2026-01-01T00:00:00Z"}, "status": {}})
    assert _pod_stuck({"metadata": {}, "status": {"phase": "Unknown"}})
    assert _pod_stuck({"metadata": {}, "status": {"phase": "Failed"}})
    assert not _pod_stuck({"metadata": {}, "status": {"phase": "Running"}})


def test_boot_heal_templates_present() -> None:
    mod = Path(__file__).resolve().parents[2] / "terraform" / "modules" / "k3s-dev-host" / "templates"
    preflight = (mod / "k3s-preflight.sh.tpl").read_text(encoding="utf-8")
    post = (mod / "post-k3s-heal.sh.tpl").read_text(encoding="utf-8")
    dropin = (mod / "prodavan-boot-heal.conf.tpl").read_text(encoding="utf-8")
    assert "docker.socket" in preflight
    assert "traefik" in post
    assert "%%{http_code}" in post
    assert "ExecStartPost" in dropin
    assert "TimeoutStopSec=30" in dropin
    assert "post-k3s-heal.sh" in dropin
    assert ">>/var/log/prodavan-post-k3s-heal.log" in dropin


def test_k3s_dev_host_crlf_strip_not_nested_in_bash_lc_quotes() -> None:
    """Regression: bash -lc '… tr -d '\\r' …' → tr -d r and strips every letter r."""
    main_tf = (
        Path(__file__).resolve().parents[2]
        / "terraform"
        / "modules"
        / "k3s-dev-host"
        / "main.tf"
    ).read_text(encoding="utf-8")
    # Ревизия provisioner-контракта живёт в locals (k3s_rev) и оттуда берётся
    # в triggers k3s_server — проверяем смысл, а не выравнивание terraform fmt
    # (буквальное `rev       = "v14-clean-install"` разъехалось бы с `rev =
    # local.k3s_rev` + `k3s_rev = "v14-clean-install"`).
    assert re.search(r'^\s*k3s_rev\s*=\s*"v14-clean-install"', main_tf, re.M)
    assert re.search(r"^\s*rev\s*=\s*local\.k3s_rev\s*$", main_tf, re.M)
    assert "prodavan-k3s-preflight.lf" in main_tf
    assert "read_bytes().replace" in main_tf
    assert "tr -d '\\r' < /tmp/prodavan-k3s-preflight.sh" not in main_tf
    assert "tr -d '\\r' < /tmp/prodavan-post-k3s-heal.sh" not in main_tf


def test_k3s_uninstall_trigger_has_only_ssh_coords() -> None:
    """Добавление любого ключа в triggers k3s_uninstall пересоздаёт ресурс.

    Пересоздание = прогон destroy-provisioner'а, то есть k3s-uninstall посреди
    обычного apply. Поэтому там только SSH-координаты; пути для очистки
    выводятся внутри скрипта из ssh_user.
    """
    main_tf = (
        Path(__file__).resolve().parents[2]
        / "terraform"
        / "modules"
        / "k3s-dev-host"
        / "main.tf"
    ).read_text(encoding="utf-8")
    start = main_tf.index('resource "null_resource" "k3s_uninstall"')
    end = main_tf.index('resource "null_resource" "gitops_bootstrap"')
    block = main_tf[start:end]
    t_start = block.index("triggers = {") + len("triggers = {")
    t_end = block.index("\n  }", t_start)
    keys = {
        line.strip().split("=")[0].strip()
        for line in block[t_start:t_end].splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    assert keys == {"cluster", "ssh_host", "ssh_port", "ssh_user", "ssh_key_path"}
    # Снос должен быть полным, а не «только k3s-uninstall.sh».
    code_lines = [ln for ln in block.splitlines() if not ln.strip().startswith("#")]
    code = "\n".join(code_lines)
    assert "on_failure = continue" not in code
    assert "/var/lib/rancher/k3s" in block
    assert "/usr/local/lib/prodavan" in block


def test_force_reinstall_wipes_before_install() -> None:
    """Чистая установка: wipe обязан стоять ДО curl get.k3s.io и записи config."""
    main_tf = (
        Path(__file__).resolve().parents[2]
        / "terraform"
        / "modules"
        / "k3s-dev-host"
        / "main.tf"
    ).read_text(encoding="utf-8")
    wipe = main_tf.index('FORCE-REINSTALL: wiping previous k3s state')
    # Инсталлер качается curl с флагами ретраев для холодной VM (--retry и т.п.) —
    # проверяем сам факт скачивания с get.k3s.io, а не точный набор флагов.
    install_match = re.search(r"curl[^\n]*https://get\.k3s\.io", main_tf)
    assert install_match, "в main.tf нет скачивания установщика k3s с get.k3s.io"
    install = install_match.start()
    cfg = main_tf.index("prodavan-k3s-config.yaml /etc/rancher/k3s/config.yaml")
    assert wipe < install, "wipe должен быть до установки k3s"
    assert wipe < cfg, "wipe удаляет /etc/rancher/k3s — config.yaml пишется после"


def test_gitops_bootstrap_fails_loudly_without_ghcr_token() -> None:
    """Пустой TF_VAR_ghcr_token — понятная ошибка, а не 12-минутный таймаут."""
    tpl = (
        Path(__file__).resolve().parents[2]
        / "terraform"
        / "modules"
        / "k3s-dev-host"
        / "templates"
        / "gitops-bootstrap.sh.tpl"
    ).read_text(encoding="utf-8")
    assert "TF_VAR_ghcr_token is empty" in tpl
    assert "WARN: no TF_VAR_ghcr_token" not in tpl



def test_rollout_timeout_prints_pod_state(monkeypatch, capsys) -> None:
    """Таймаут без диагностики не отличим от любой другой причины."""

    from types import SimpleNamespace

    from prodavan_ops import k8s

    class _Dep:
        metadata = SimpleNamespace(generation=2)
        spec = SimpleNamespace(replicas=1)
        status = SimpleNamespace(
            ready_replicas=0, updated_replicas=1, observed_generation=2
        )

    class _Apps:
        def read_namespaced_deployment(self, *, name, namespace):
            return _Dep()

        def patch_namespaced_deployment(self, **kwargs):
            return None

    crashing = SimpleNamespace(
        name="worker",
        ready=False,
        restart_count=7,
        state=SimpleNamespace(
            waiting=SimpleNamespace(
                reason="CrashLoopBackOff", message="back-off 40s restarting",
            ),
            terminated=None,
        ),
    )
    pod = SimpleNamespace(
        metadata=SimpleNamespace(name="agentscale-celery-worker-abc"),
        status=SimpleNamespace(phase="Running", container_statuses=[crashing]),
    )

    class _Core:
        def list_namespaced_pod(self, *, namespace, label_selector):
            return SimpleNamespace(items=[pod])

        def read_namespaced_pod_log(self, **kwargs):
            return "Traceback: RuntimeError: boom"

    monkeypatch.setattr(k8s, "require_k8s_api", lambda: None)
    monkeypatch.setattr(k8s, "load_kube", lambda: None)
    monkeypatch.setattr(k8s.client, "AppsV1Api", lambda: _Apps())
    monkeypatch.setattr(k8s.client, "CoreV1Api", lambda: _Core())
    monkeypatch.setattr(k8s.time, "sleep", lambda _s: None)

    with pytest.raises(TimeoutError):
        k8s.rollout_restart(namespace="agentscale-dev", deployments=("agentscale-celery-worker",), timeout_sec=0)

    out = capsys.readouterr().out
    assert "rollout diag: pod agentscale-celery-worker-abc" in out
    assert "reason=CrashLoopBackOff" in out
    assert "restarts=7" in out
    assert "RuntimeError: boom" in out
