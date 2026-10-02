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
    "agentscale-api",
    "agentscale-web",
    "agentscale-celery-worker",
    "agentscale-celery-beat",
)

# Deployment targets for rollout: (name, namespace) pairs. The probe pod
# (agent-runtime) lives in agentscale-dev-sandboxes (not agentscale), so rollout must
# cross namespaces. After a fresh prodavan-agent-runtime:latest is pushed by
# openclaw-images (claw repo), rollout restarts this so the probe pod re-pulls
# the new digest (imagePullPolicy: Always). Project sandbox pods are dynamic
# (per-project, created by pod_service) and re-pull on recreate by design.
DEPLOYMENT_TARGETS: tuple[tuple[str, str], ...] = (
    ("agentscale-api", "agentscale-dev"),
    ("agentscale-web", "agentscale-dev"),
    ("agentscale-celery-worker", "agentscale-dev"),
    ("agentscale-celery-beat", "agentscale-dev"),
    ("agentscale-probe-pod", "agentscale-dev-sandboxes"),
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
    if kube.name == "prodavan-kube-docker.yaml" and kube.is_file():
        _cached_kubeconfig = kube
        return kube
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
        proc = kubectl(["get", "ns", "agentscale-dev", "--request-timeout=5s"], retries=1)
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


def kubectl(
    args: list[str],
    *,
    retries: int = 2,
    retry_sleep: float = 1.0,
    subprocess_timeout: float = 30.0,
    request_timeout: str = "15s",
) -> subprocess.CompletedProcess[str]:
    """Run kubectl with rewritten kubeconfig; retry transient API/ TLS blips."""
    path = resolve_kubeconfig_path()
    cmd_args = list(args)
    if not any(a.startswith("--request-timeout") for a in cmd_args):
        cmd_args.append(f"--request-timeout={request_timeout}")
    last: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, retries + 1):
        try:
            proc = subprocess.run(
                ["kubectl", "--kubeconfig", str(path), *cmd_args],
                capture_output=True,
                text=True,
                check=False,
                timeout=subprocess_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            err = "kubectl subprocess timed out"
            if exc.stderr:
                err = exc.stderr if isinstance(exc.stderr, str) else exc.stderr.decode(errors="replace")
            proc = subprocess.CompletedProcess(
                args=exc.cmd or ["kubectl", *cmd_args],
                returncode=124,
                stdout=exc.stdout or "",
                stderr=err,
            )
        last = proc
        if proc.returncode == 0:
            return proc
        err = (proc.stderr or proc.stdout or "").strip()
        tail = err.splitlines()[-1] if err else f"exit {proc.returncode}"
        transient = proc.returncode == 124 or any(
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
        print(f"kubectl retry {attempt}/{retries} ({' '.join(cmd_args[:3])}): {tail}")
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


def _is_api_unreachable(exc: BaseException) -> bool:
    if isinstance(exc, ApiException) and exc.status in (0, None):
        return True
    if isinstance(exc, ApiException) and isinstance(exc.body, str):
        body = exc.body.lower()
        if any(t in body for t in ("connection", "eof", "timeout", "refused", "unable to connect")):
            return True
    msg = str(exc).lower()
    return any(
        t in msg
        for t in (
            "connection refused",
            "connection reset",
            "unable to connect",
            "eof",
            "timed out",
            "timeout",
            "max retries exceeded",
            "failed to establish",
            "name or service not known",
        )
    )


def require_k8s_api(*, timeout_sec: int = 15) -> None:
    """Fail fast before long rollout/Argo waits if cluster API is down."""
    wait_k3s_api_ready(timeout_sec=timeout_sec)


def _fail_unreachable(exc: BaseException, *, streak: int, limit: int = 3) -> None:
    if _is_api_unreachable(exc) and streak >= limit:
        raise RuntimeError(
            f"Kubernetes API unreachable ({exc}). "
            "On Windows host run elevated: infra/github-runner/Sync-KubeForDocker.ps1"
        ) from exc


def rollout_restart(
    namespace: str | None = None,
    deployments: tuple[str, ...] | None = None,
    timeout_sec: int = 600,
) -> None:
    """Patch pod-template annotation so kubelet re-pulls :latest (Always).

    By default restarts all DEPLOYMENT_TARGETS (agentscale namespace: api/web/
    celery-worker/celery-beat; agentscale-dev-sandboxes: agentscale-probe-pod). When
    `namespace` + `deployments` are passed, overrides the default target set
    (legacy single-namespace call path).
    """
    require_k8s_api()
    load_kube()
    apps = client.AppsV1Api()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if namespace is not None and deployments is not None:
        targets: tuple[tuple[str, str], ...] = tuple((n, namespace) for n in deployments)
    else:
        targets = DEPLOYMENT_TARGETS
    for name, ns in targets:
        body = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "agentscale.io/restartedAt": stamp,
                        }
                    }
                }
            }
        }
        try:
            apps.patch_namespaced_deployment(name=name, namespace=ns, body=body)
        except ApiException as exc:
            _fail_unreachable(exc, streak=1, limit=1)
            raise
        except Exception as exc:
            _fail_unreachable(exc, streak=1, limit=1)
            raise
        print(f"rollout restart requested: {ns}/{name}")
    deadline = time.time() + timeout_sec
    for name, ns in targets:
        unreachable_streak = 0
        while time.time() < deadline:
            try:
                dep = apps.read_namespaced_deployment(name=name, namespace=ns)
            except ApiException as exc:
                unreachable_streak += 1
                _fail_unreachable(exc, streak=unreachable_streak)
                raise
            except Exception as exc:
                unreachable_streak += 1
                _fail_unreachable(exc, streak=unreachable_streak)
                raise
            unreachable_streak = 0
            status = dep.status
            desired = dep.spec.replicas or 0
            updated = (status.updated_replicas or 0) if status else 0
            ready = (status.ready_replicas or 0) if status else 0
            gen = dep.metadata.generation or 0
            obs = (status.observed_generation or 0) if status else 0
            if obs >= gen and updated >= desired and ready >= desired and desired > 0:
                print(f"rollout ok: {ns}/{name} ready={ready}/{desired}")
                break
            print(f"rollout wait: {ns}/{name} ready={ready} updated={updated} desired={desired}")
            time.sleep(5)
        else:
            raise TimeoutError(f"rollout timed out: {ns}/{name}")


def wait_argo_app(
    name: str = "agentscale-dev",
    namespace: str = "argocd",
    timeout_sec: int = 600,
    poll_sec: float = 5.0,
) -> None:
    """Wait until Argo Application is Synced + Healthy."""
    require_k8s_api()
    load_kube()
    api = client.CustomObjectsApi()
    deadline = time.time() + timeout_sec
    last = ""
    unreachable_streak = 0
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
            unreachable_streak += 1
            _fail_unreachable(exc, streak=unreachable_streak)
            last = f"get Application failed: {exc.status} {exc.reason}"
            time.sleep(poll_sec)
            continue
        except Exception as exc:
            unreachable_streak += 1
            _fail_unreachable(exc, streak=unreachable_streak)
            raise
        unreachable_streak = 0
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
        except ApiException as exc:
            unreachable_streak += 1
            _fail_unreachable(exc, streak=unreachable_streak)
        except Exception as exc:
            unreachable_streak += 1
            _fail_unreachable(exc, streak=unreachable_streak)
            raise
        time.sleep(poll_sec)
    raise TimeoutError(f"Argo Application {name} not ready within {timeout_sec}s ({last})")
