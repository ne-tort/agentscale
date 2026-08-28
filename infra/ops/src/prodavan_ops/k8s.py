from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from prodavan_ops.paths import default_kubeconfig

FIRST_PARTY_DEPLOYMENTS = (
    "prodavan-api",
    "prodavan-web",
    "prodavan-celery-worker",
    "prodavan-celery-beat",
)

_cached_kubeconfig: Path | None = None


def prepare_docker_kubeconfig(
    src: Path,
    dest: Path,
    *,
    server_host: str = "host.docker.internal",
    api_port: int = 6443,
) -> None:
    """Rewrite server for Docker Desktop runners; strip BOM; skip TLS verify (local-dev)."""
    text = src.read_text(encoding="utf-8-sig")
    text = re.sub(
        r"server:\s*https://\S+",
        f"server: https://{server_host}:{api_port}",
        text,
        count=1,
    )
    # kubectl 1.31+ rejects insecure-skip-tls-verify together with certificate-authority(-data).
    text = re.sub(r"(?m)^\s*certificate-authority-data:\s*\S+\n", "", text)
    text = re.sub(r"(?m)^\s*certificate-authority:\s*\S+\n", "", text)
    if re.search(r"insecure-skip-tls-verify:\s*", text):
        text = re.sub(
            r"insecure-skip-tls-verify:\s*\S+",
            "insecure-skip-tls-verify: true",
            text,
            count=1,
        )
    else:
        # k3s kubeconfig: "clusters:\n- cluster:\n    server: ..."
        text = re.sub(
            r"(?m)^(\s*-\s*cluster:\s*\n)",
            r"\1    insecure-skip-tls-verify: true\n",
            text,
            count=1,
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")


def resolve_kubeconfig_path() -> Path:
    """Use KUBECONFIG; for Docker runners rewrite once to temp with skip-tls."""
    global _cached_kubeconfig
    if _cached_kubeconfig is not None:
        return _cached_kubeconfig

    kube = Path(os.environ.get("KUBECONFIG") or str(default_kubeconfig()))
    expect = os.environ.get("PRODAVAN_CI_HOST", "").strip()
    if expect != "host.docker.internal":
        _cached_kubeconfig = kube
        return kube
    if not kube.is_file():
        raise RuntimeError(f"KUBECONFIG not found: {kube}")
    base = Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir())
    dest = base / "prodavan-kube-docker.yaml"
    prepare_docker_kubeconfig(kube, dest, server_host=expect)
    os.environ["KUBECONFIG"] = str(dest)
    print(f"kubeconfig for Docker gateway -> {dest}", file=sys.stderr)
    _cached_kubeconfig = dest
    return dest


def load_kube() -> None:
    path = resolve_kubeconfig_path()
    config.load_kube_config(config_file=str(path))


def wait_k3s_api_ready(*, timeout_sec: int = 30, poll_sec: float = 2.0) -> None:
    """Fail fast if k3s API is unreachable from the runner (no long CI waits)."""
    deadline = time.time() + timeout_sec
    attempt = 0
    last = ""
    while time.time() < deadline:
        attempt += 1
        proc = kubectl(["get", "ns", "prodavan", "--request-timeout=5s"], retries=1)
        if proc.returncode == 0:
            if attempt > 1:
                print(f"k3s API ready (attempt {attempt})")
            return
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        last = err[-1] if err else "no response"
        print(f"k3s API not ready ({attempt}): {last}")
        time.sleep(poll_sec)
    raise TimeoutError(
        f"k3s API not ready within {timeout_sec}s ({last}). "
        "On Windows host run elevated: infra/github-runner/Sync-KubeForDocker.ps1"
    )


def kubectl(args: list[str], *, retries: int = 2, retry_sleep: float = 1.0) -> subprocess.CompletedProcess[str]:
    """Run kubectl with rewritten kubeconfig; retry transient API/ TLS blips."""
    path = resolve_kubeconfig_path()
    last: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, retries + 1):
        proc = subprocess.run(
            ["kubectl", "--kubeconfig", str(path), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        last = proc
        if proc.returncode == 0:
            return proc
        err = (proc.stderr or proc.stdout or "").strip()
        tail = err.splitlines()[-1] if err else f"exit {proc.returncode}"
        transient = any(
            token in err
            for token in (
                "EOF",
                "timeout",
                "Timeout",
                "deadline exceeded",
                "connection reset",
                "TLS",
                "SSL",
                "Unable to connect",
            )
        )
        if not transient or attempt >= retries:
            return proc
        print(f"kubectl retry {attempt}/{retries} ({' '.join(args[:3])}): {tail}")
        time.sleep(retry_sleep)
    assert last is not None
    return last


def assert_kubeconfig_docker_ready() -> None:
    """Fail fast / auto-rewrite when runners expect host.docker.internal."""
    expect_host = os.environ.get("PRODAVAN_CI_HOST", "").strip()
    if expect_host != "host.docker.internal":
        return
    path = resolve_kubeconfig_path()
    text = path.read_text(encoding="utf-8-sig")
    if "host.docker.internal" not in text:
        raise RuntimeError(
            f"KUBECONFIG={path} must use server https://host.docker.internal:6443 "
            "(run infra/github-runner/Sync-KubeForDocker.ps1 as Admin on Windows)"
        )


def rollout_restart(
    namespace: str = "prodavan",
    deployments: tuple[str, ...] = FIRST_PARTY_DEPLOYMENTS,
    timeout_sec: int = 600,
) -> None:
    """Patch pod-template annotation so kubelet re-pulls :latest (Always)."""
    load_kube()
    apps = client.AppsV1Api()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for name in deployments:
        body = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "prodavan.io/restartedAt": stamp,
                        }
                    }
                }
            }
        }
        apps.patch_namespaced_deployment(name=name, namespace=namespace, body=body)
        print(f"rollout restart requested: {namespace}/{name}")
    deadline = time.time() + timeout_sec
    for name in deployments:
        while time.time() < deadline:
            dep = apps.read_namespaced_deployment(name=name, namespace=namespace)
            status = dep.status
            desired = dep.spec.replicas or 0
            updated = (status.updated_replicas or 0) if status else 0
            ready = (status.ready_replicas or 0) if status else 0
            gen = dep.metadata.generation or 0
            obs = (status.observed_generation or 0) if status else 0
            if obs >= gen and updated >= desired and ready >= desired and desired > 0:
                print(f"rollout ok: {namespace}/{name} ready={ready}/{desired}")
                break
            print(f"rollout wait: {namespace}/{name} ready={ready} updated={updated} desired={desired}")
            time.sleep(5)
        else:
            raise TimeoutError(f"rollout timed out: {namespace}/{name}")


def wait_argo_app(
    name: str = "prodavan-dev",
    namespace: str = "argocd",
    timeout_sec: int = 600,
    poll_sec: float = 5.0,
) -> None:
    """Wait until Argo Application is Synced + Healthy."""
    load_kube()
    api = client.CustomObjectsApi()
    deadline = time.time() + timeout_sec
    last = ""
    while time.time() < deadline:
        try:
            obj = api.get_namespaced_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                namespace=namespace,
                plural="applications",
                name=name,
            )
        except ApiException as exc:
            last = f"get Application failed: {exc.status} {exc.reason}"
            time.sleep(poll_sec)
            continue
        status = (obj or {}).get("status") or {}
        sync = ((status.get("sync") or {}).get("status")) or "Unknown"
        health = ((status.get("health") or {}).get("status")) or "Unknown"
        last = f"sync={sync} health={health}"
        print(last)
        if sync == "Synced" and health == "Healthy":
            print(f"Argo Application {name} OK")
            return
        try:
            meta = obj.setdefault("metadata", {})
            ann = meta.setdefault("annotations", {})
            ann["argocd.argoproj.io/refresh"] = "hard"
            api.patch_namespaced_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                namespace=namespace,
                plural="applications",
                name=name,
                body={"metadata": {"annotations": ann}},
            )
        except ApiException:
            pass
        time.sleep(poll_sec)
    raise TimeoutError(f"Argo Application {name} not ready within {timeout_sec}s ({last})")
