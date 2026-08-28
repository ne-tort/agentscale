"""E2e runner — apply ephemeral overlay, wait Job, fetch logs."""

from __future__ import annotations

import time

from kubernetes import client
from kubernetes.client.rest import ApiException

from prodavan_ops.k8s import kubectl, load_kube
from prodavan_ops.paths import overlay_e2e

E2E_NAMESPACE = "prodavan"
E2E_JOB = "prodavan-e2e-runner"
_API_FAIL_STREAK = 0


def _apply_e2e_overlay() -> None:
    """Apply overlays/e2e (ephemeral Job only — dev API stays stub)."""
    path = overlay_e2e()
    proc = kubectl(["apply", "-k", str(path)], retries=2)
    if proc.returncode != 0:
        raise RuntimeError(
            f"kubectl apply -k {path} failed ({proc.returncode})\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    print(proc.stdout.strip() or f"applied {path}")


def _delete_job(*, fast: bool = False) -> None:
    proc = kubectl(
        [
            "delete",
            "job",
            E2E_JOB,
            "-n",
            E2E_NAMESPACE,
            "--ignore-not-found",
            "--wait=false",
        ],
        retries=1 if fast else 2,
        subprocess_timeout=20.0 if fast else 30.0,
        request_timeout="10s" if fast else "15s",
    )
    if proc.returncode == 0 and proc.stdout.strip():
        print(proc.stdout.strip())
    elif proc.returncode != 0:
        print(f"skip job delete: {proc.stderr.strip() or proc.stdout.strip()}")
        return


def _job_counts() -> tuple[int, int, int] | None:
    global _API_FAIL_STREAK
    proc = kubectl(
        [
            "get",
            "job",
            E2E_JOB,
            "-n",
            E2E_NAMESPACE,
            "-o",
            "jsonpath={.status.succeeded},{.status.failed},{.status.active}",
        ],
        retries=2,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        if "NotFound" in err:
            _API_FAIL_STREAK = 0
            return None
        if any(
            token in err
            for token in ("EOF", "timeout", "Timeout", "Unable to connect", "connection refused", "TLS", "SSL")
        ):
            _API_FAIL_STREAK += 1
            if _API_FAIL_STREAK >= 3:
                raise RuntimeError(
                    f"Kubernetes API unreachable while polling Job ({err.splitlines()[-1] if err else proc.returncode})"
                )
        print(f"kubectl get job transient error: {err.splitlines()[-1] if err else proc.returncode}")
        return None
    _API_FAIL_STREAK = 0
    parts = (proc.stdout or "0,0,0").split(",")
    while len(parts) < 3:
        parts.append("0")

    def _int(value: str) -> int:
        value = (value or "").strip()
        return int(value) if value.isdigit() else 0

    return _int(parts[0]), _int(parts[1]), _int(parts[2])


def _wait_job(timeout_sec: int = 900) -> None:
    deadline = time.time() + timeout_sec
    last = ""
    while time.time() < deadline:
        counts = _job_counts()
        if counts is None:
            last = "Job not found yet"
            print(f"e2e job: {last}")
            time.sleep(3)
            continue
        succeeded, failed, active = counts
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
    proc = kubectl(
        [
            "logs",
            "-n",
            E2E_NAMESPACE,
            "-l",
            f"job-name={E2E_JOB}",
            "--all-containers=true",
            "--tail=-1",
        ],
        retries=2,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        print(f"--- logs {E2E_JOB} ---\n{proc.stdout}")
        return
    err = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
    print(f"--- logs {E2E_JOB} unavailable: {err} ---")


def run_e2e(*, suite: str = "k8s", timeout_sec: int = 900) -> None:
    """Run cluster e2e suite (currently k8s pod tests via in-cluster Job)."""
    if suite not in {"k8s", "all"}:
        raise ValueError(f"prodavan-ops e2e run supports suite k8s|all (got {suite!r})")
    print("==> e2e cleanup (prior Job)")
    cleanup_e2e()
    print("==> apply overlays/e2e")
    _apply_e2e_overlay()
    print(f"==> wait Job {E2E_NAMESPACE}/{E2E_JOB}")
    _wait_job(timeout_sec=timeout_sec)
    print("prodavan-ops e2e run OK")


def cleanup_e2e(*, unsync: bool = False) -> None:
    """Delete e2e Job and overlay resources (best-effort, bounded timeouts)."""
    _delete_job(fast=True)
    path = overlay_e2e()
    proc = kubectl(
        ["delete", "-k", str(path), "--ignore-not-found", "--wait=false"],
        retries=1,
        subprocess_timeout=20.0,
        request_timeout="10s",
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
