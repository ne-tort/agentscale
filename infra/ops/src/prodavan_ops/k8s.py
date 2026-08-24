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
