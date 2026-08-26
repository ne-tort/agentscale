from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import yaml

from prodavan_ops.paths import overlay_dev, repo_root

FIRST_PARTY_LATEST = {
    "ghcr.io/ne-tort/prodavan-api:latest",
    "ghcr.io/ne-tort/prodavan-web:latest",
}
FORBIDDEN_INFRA_LATEST = re.compile(
    r"^(postgres:(16|latest)|redis:(7-alpine|7|latest)|minio/minio:latest|minio/mc:latest)$"
)
REQUIRED_SNIPPETS = (
    "postgres:16.15",
    "redis:7.4.11-alpine",
    "minio/minio:RELEASE.2024-10-02T17-50-41Z",
    "redpanda:v24.2.4",
)


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None) -> str:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=merged,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc.stdout


def render_overlay() -> str:
    """Render overlays/dev via kubectl or kustomize on PATH (host tools)."""
    path = overlay_dev()
    if shutil.which("kubectl"):
        return _run(["kubectl", "kustomize", str(path)])
    if shutil.which("kustomize"):
        return _run(["kustomize", "build", str(path)])
    raise RuntimeError("need kubectl or kustomize on PATH for validate")


def render_argocd_install() -> str:
    """Render Argo CD install kustomize (remote upstream + patches)."""
    path = repo_root() / "infra" / "argocd" / "install"
    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            if shutil.which("kubectl"):
                return _run(["kubectl", "kustomize", str(path)])
            if shutil.which("kustomize"):
                return _run(["kustomize", "build", str(path)])
            raise RuntimeError("need kubectl or kustomize on PATH for validate")
        except Exception as exc:  # noqa: BLE001 — retry remote fetch flakes
            last_err = exc
            print(f"argocd/install render attempt {attempt}/3 failed: {exc}")
    assert last_err is not None
    raise last_err


def _images_from_manifest(manifest: str) -> list[str]:
    images: list[str] = []
    for doc in yaml.safe_load_all(manifest):
        if not isinstance(doc, dict):
            continue
        for img in _walk_images(doc):
            images.append(img)
    for m in re.finditer(r"(?m)^[ \t]+image:[ \t]+(\S+)\s*$", manifest):
        images.append(m.group(1))
    return images


def _walk_images(node: object) -> list[str]:
    out: list[str] = []
    if isinstance(node, dict):
        if "image" in node and isinstance(node["image"], str):
            out.append(node["image"])
        for v in node.values():
            out.extend(_walk_images(v))
    elif isinstance(node, list):
        for item in node:
            out.extend(_walk_images(item))
    return out


def verify_image_pins(manifest: str) -> None:
    first_party = 0
    for img in _images_from_manifest(manifest):
        if img in FIRST_PARTY_LATEST:
            first_party += 1
            continue
        if img.startswith("ghcr.io/ne-tort/prodavan-api:") or img.startswith(
            "ghcr.io/ne-tort/prodavan-web:"
        ):
            raise RuntimeError(f"first-party image must be :latest (got {img})")
        if FORBIDDEN_INFRA_LATEST.match(img):
            raise RuntimeError(f"infra image must be a frozen version tag (got {img})")
        if img.endswith(":latest"):
            raise RuntimeError(f"third-party must not use :latest (got {img})")
    if first_party < 2:
        raise RuntimeError("expected api+web :latest in overlay render")
    for snippet in REQUIRED_SNIPPETS:
        if snippet not in manifest:
            raise RuntimeError(f"missing required pin snippet: {snippet}")


def assert_no_shell_scripts() -> None:
    """Enforce GitOps contract: no .sh under infra/."""
    root = repo_root() / "infra"
    banned = [p.relative_to(repo_root()) for p in root.rglob("*.sh")]
    if banned:
        listing = "\n".join(f"  - {p}" for p in sorted(banned))
        raise RuntimeError(f"forbidden .sh under infra/:\n{listing}")


def assert_no_compose_or_k3d() -> None:
    """Cluster path is k3s+Argo only — no compose-as-cluster, no k3d, no duplicate deploy trees.

    Exception: infra/github-runner/docker-compose.yml — Actions runners on Docker Desktop
    (not the app cluster).
    """
    root = repo_root()
    banned: list[Path] = []
    for p in (root / "infra").rglob("docker-compose*.yml"):
        rel = p.relative_to(root)
        if rel.parts[:2] == ("infra", "github-runner"):
            continue
        banned.append(rel)
    for rel in ("infra/k3d", "infra/k8s", "deploy/k8s", "deploy"):
        p = root / rel
        if p.exists():
            banned.append(p.relative_to(root))
    if banned:
        listing = "\n".join(f"  - {x}" for x in sorted(set(banned)))
        raise RuntimeError(f"forbidden legacy deploy paths:\n{listing}")


def validate_all() -> None:
    print("==> no .sh under infra/")
    assert_no_shell_scripts()
    print("ok")

    print("==> no legacy deploy paths (compose/k3d/deploy/k8s)")
    assert_no_compose_or_k3d()
    print("ok")

    print("==> kustomize overlays/dev")
    manifest = render_overlay()
    lines = manifest.count("\n") + (1 if manifest and not manifest.endswith("\n") else 0)
    print(f"ok kustomize ({lines} lines)")

    print("==> kustomize argocd/install")
    argo_manifest = render_argocd_install()
    argo_lines = argo_manifest.count("\n") + (
        1 if argo_manifest and not argo_manifest.endswith("\n") else 0
    )
    if "kind: Deployment" not in argo_manifest or "argocd-server" not in argo_manifest:
        raise RuntimeError("argocd/install render missing expected Deployments")
    print(f"ok kustomize ({argo_lines} lines)")

    print("==> image pins")
    verify_image_pins(manifest)
    print("ok")

    print("==> first-party Always pull + ghcr-pull")
    if "imagePullPolicy: Always" not in manifest:
        raise RuntimeError("dev overlay must set imagePullPolicy: Always for :latest")
    if "name: ghcr-pull" not in manifest:
        raise RuntimeError("dev overlay must reference imagePullSecrets ghcr-pull")
    _require_api_migrate_always(manifest)
    print("ok")

    print("prodavan-ops validate OK")


def _require_api_migrate_always(manifest: str) -> None:
    """migrate initContainer must Always-pull :latest (else schema skew vs api)."""
    for doc in yaml.safe_load_all(manifest):
        if not isinstance(doc, dict):
            continue
        if doc.get("kind") != "Deployment":
            continue
        meta = doc.get("metadata") or {}
        if meta.get("name") != "prodavan-api":
            continue
        inits = ((doc.get("spec") or {}).get("template") or {}).get("spec", {}).get(
            "initContainers"
        ) or []
        migrate = next((c for c in inits if isinstance(c, dict) and c.get("name") == "migrate"), None)
        if migrate is None:
            raise RuntimeError("prodavan-api must define initContainer migrate")
        if migrate.get("imagePullPolicy") != "Always":
            raise RuntimeError(
                "prodavan-api migrate initContainer must use imagePullPolicy: Always "
                "(IfNotPresent caches an old head → upgrade noop + API 500s)"
            )
        return
    raise RuntimeError("prodavan-api Deployment missing from overlays/dev render")
