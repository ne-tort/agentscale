"""E2e runner — apply ephemeral overlay, wait Job, fetch logs."""

from __future__ import annotations

import subprocess
import time

from kubernetes import client
from kubernetes.client.rest import ApiException

from prodavan_ops.k8s import load_kube, wait_k3s_api_ready
from prodavan_ops.paths import overlay_e2e

E2E_NAMESPACE = "prodavan"
E2E_JOB = "prodavan-e2e-runner"


def _apply_e2e_overlay() -> None:
    """Apply overlays/e2e (ephemeral Job only — dev API stays stub)."""
    path = overlay_e2e()
    proc = subprocess.run(
        ["kubectl", "apply", "-k", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"kubectl apply -k {path} failed ({proc.returncode})\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    print(proc.stdout.strip() or f"applied {path}")


def _delete_job() -> None:
    try:
        load_kube()
    except Exception as exc:
        print(f"skip job delete: kube unavailable ({exc})")
        return
    batch = client.BatchV1Api()
    try:
        batch.delete_namespaced_job(
            name=E2E_JOB,
            namespace=E2E_NAMESPACE,
            propagation_policy="Background",
        )
        print(f"deleted Job {E2E_NAMESPACE}/{E2E_JOB}")
    except ApiException as exc:
        if exc.status != 404:
            print(f"skip job delete: {exc.status} {exc.reason}")
            return
    except Exception as exc:
        print(f"skip job delete: {exc}")
        return
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            batch.read_namespaced_job(name=E2E_JOB, namespace=E2E_NAMESPACE)
            time.sleep(2)
        except ApiException as exc:
            if exc.status == 404:
                return
            print(f"skip job wait: {exc.status} {exc.reason}")
            return
        except Exception as exc:
            print(f"skip job wait: {exc}")
            return


def _wait_job(timeout_sec: int = 900) -> None:
    load_kube()
    batch = client.BatchV1Api()
    deadline = time.time() + timeout_sec
    last = ""
    while time.time() < deadline:
        try:
            job = batch.read_namespaced_job(name=E2E_JOB, namespace=E2E_NAMESPACE)
        except ApiException as exc:
            if exc.status == 404:
                last = "Job not found yet"
                time.sleep(3)
                continue
            raise
        status = job.status
        succeeded = (status.succeeded or 0) if status else 0
        failed = (status.failed or 0) if status else 0
        active = (status.active or 0) if status else 0
        last = f"active={active} succeeded={succeeded} failed={failed}"
        print(f"e2e job: {last}")
        if succeeded >= 1:
            print("e2e Job succeeded")
            return
        if failed >= 1:
            _print_job_logs()
            raise RuntimeError(f"e2e Job failed ({last})")
        time.sleep(5)
    _print_job_logs()
    raise TimeoutError(f"e2e Job timed out after {timeout_sec}s ({last})")


def _print_job_logs() -> None:
    load_kube()
    core = client.CoreV1Api()
    pods = core.list_namespaced_pod(
        namespace=E2E_NAMESPACE,
        label_selector=f"job-name={E2E_JOB}",
    )
    for pod in pods.items or []:
        name = pod.metadata.name
        try:
            logs = core.read_namespaced_pod_log(name=name, namespace=E2E_NAMESPACE)
            print(f"--- logs {name} ---\n{logs}")
        except ApiException as exc:
            print(f"--- logs {name} unavailable: {exc.status} ---")


def run_e2e(*, suite: str = "k8s", timeout_sec: int = 900) -> None:
    """Run cluster e2e suite (currently k8s pod tests via in-cluster Job)."""
    if suite not in {"k8s", "all"}:
        raise ValueError(f"prodavan-ops e2e run supports suite k8s|all (got {suite!r})")
    print("==> wait k3s API")
    wait_k3s_api_ready(timeout_sec=300)
    print("==> e2e cleanup (prior Job)")
    cleanup_e2e()
    print("==> apply overlays/e2e")
    _apply_e2e_overlay()
    print(f"==> wait Job {E2E_NAMESPACE}/{E2E_JOB}")
    _wait_job(timeout_sec=timeout_sec)
    print("prodavan-ops e2e run OK")


def cleanup_e2e(*, unsync: bool = False) -> None:
    """Delete e2e Job and overlay resources."""
    _delete_job()
    path = overlay_e2e()
    proc = subprocess.run(
        ["kubectl", "delete", "-k", str(path), "--ignore-not-found"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(proc.stderr.strip() or f"kubectl delete -k {path} failed ({proc.returncode})")
    if unsync:
        try:
            load_kube()
        except Exception as exc:
            print(f"skip argo unsync: kube unavailable ({exc})")
        else:
            api = client.CustomObjectsApi()
            try:
                api.delete_namespaced_custom_object(
                    group="argoproj.io",
                    version="v1alpha1",
                    namespace="argocd",
                    plural="applications",
                    name="prodavan-e2e",
                )
            except ApiException as exc:
                if exc.status != 404:
                    print(f"skip argo unsync: {exc.status} {exc.reason}")
            except Exception as exc:
                print(f"skip argo unsync: {exc}")
    print("prodavan-ops e2e cleanup OK")
