from __future__ import annotations

import os
import time
from pathlib import Path

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from prodavan_ops.paths import default_kubeconfig


def load_kube() -> None:
    kube = os.environ.get("KUBECONFIG") or str(default_kubeconfig())
    path = Path(kube)
    if path.is_file():
        config.load_kube_config(config_file=str(path))
    else:
        config.load_kube_config()


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
        # nudge refresh
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


def ensure_ghcr_pull_secret(
    namespace: str = "prodavan",
    name: str = "ghcr-pull",
    server: str = "ghcr.io",
) -> None:
    """Bootstrap docker-registry pull secret from env (not stored in git)."""
    token = os.environ.get("GHCR_TOKEN") or os.environ.get("GITHUB_TOKEN")
    username = os.environ.get("GHCR_USERNAME") or os.environ.get("GITHUB_ACTOR") or "token"
    if not token:
        raise RuntimeError("set GHCR_TOKEN or GITHUB_TOKEN to create ghcr-pull secret")
    load_kube()
    v1 = client.CoreV1Api()
    import base64
    import json

    auth = base64.b64encode(f"{username}:{token}".encode()).decode()
    docker_config = {
        "auths": {
            server: {
                "username": username,
                "password": token,
                "auth": auth,
            }
        }
    }
    data = {
        ".dockerconfigjson": base64.b64encode(json.dumps(docker_config).encode()).decode()
    }
    body = client.V1Secret(
        metadata=client.V1ObjectMeta(name=name, namespace=namespace),
        type="kubernetes.io/dockerconfigjson",
        data=data,
    )
    try:
        v1.read_namespaced_secret(name, namespace)
        v1.replace_namespaced_secret(name, namespace, body)
        print(f"updated secret {namespace}/{name}")
    except ApiException as exc:
        if exc.status != 404:
            raise
        v1.create_namespaced_secret(namespace, body)
        print(f"created secret {namespace}/{name}")
