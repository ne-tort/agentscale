from __future__ import annotations

import os
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


def load_kube() -> None:
    kube = os.environ.get("KUBECONFIG") or str(default_kubeconfig())
    path = Path(kube)
    if path.is_file():
        config.load_kube_config(config_file=str(path))
    else:
        config.load_kube_config()


def assert_kubeconfig_docker_ready() -> None:
    """Fail fast when runners expect host.docker.internal but kubeconfig still points at 127.0.0.1."""
    expect_host = os.environ.get("PRODAVAN_CI_HOST", "").strip()
    if expect_host != "host.docker.internal":
        return
    kube = os.environ.get("KUBECONFIG") or str(default_kubeconfig())
    text = Path(kube).read_text(encoding="utf-8")
    if "host.docker.internal" not in text:
        raise RuntimeError(
            f"KUBECONFIG={kube} must use server https://host.docker.internal:6443 "
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
