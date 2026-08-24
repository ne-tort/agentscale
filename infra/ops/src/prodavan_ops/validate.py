from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

from prodavan_ops.paths import overlay_dev, repo_root, terraform_local

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
    """Render overlays/dev via kubectl or kustomize on PATH (host tools, no containers)."""
    path = overlay_dev()
    if shutil.which("kubectl"):
        return _run(["kubectl", "kustomize", str(path)])
    if shutil.which("kustomize"):
        return _run(["kustomize", "build", str(path)])
    raise RuntimeError(
        "need kubectl or kustomize on PATH for validate "
        "(install on runner host; cluster is k3s-only, no docker fallback)"
    )


def _images_from_manifest(manifest: str) -> list[str]:
    images: list[str] = []
    for doc in yaml.safe_load_all(manifest):
        if not isinstance(doc, dict):
            continue
        for img in _walk_images(doc):
            images.append(img)
    # fallback regex for kustomize image: lines if yaml miss
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
    """Enforce GitOps contract: no .sh under infra/ (ops is Python-only)."""
    root = repo_root() / "infra"
    banned: list[Path] = []
    for p in root.rglob("*.sh"):
        # allow nothing
        banned.append(p.relative_to(repo_root()))
    if banned:
        listing = "\n".join(f"  - {p}" for p in sorted(banned))
        raise RuntimeError(f"forbidden .sh under infra/:\n{listing}")


def terraform_validate() -> None:
    tf_dir = terraform_local()
    tf_bin = shutil.which("terraform")
    if not tf_bin:
        raise RuntimeError(
            "need terraform on PATH for validate "
            "(install on runner host; no docker fallback)"
        )
    with tempfile.TemporaryDirectory(prefix="prodavan-tf-") as tmp:
        # Copy only .tf files to avoid polluting workspace with root-owned .terraform
        work = Path(tmp) / "local"
        work.mkdir()
        for f in tf_dir.glob("*.tf"):
            shutil.copy2(f, work / f.name)
    _run([tf_bin, f"-chdir={tf_dir}", "init", "-backend=false", "-input=false"])
    _run([tf_bin, f"-chdir={tf_dir}", "validate"])
    tf_meta = tf_dir / ".terraform"
    if tf_meta.exists():
        shutil.rmtree(tf_meta, ignore_errors=True)
    lock = tf_dir / ".terraform.lock.hcl"
    if lock.exists():
        lock.unlink(missing_ok=True)


def validate_all() -> None:
    print("==> no .sh under infra/")
    assert_no_shell_scripts()
    print("ok")

    print("==> kustomize overlays/dev")
    manifest = render_overlay()
    lines = manifest.count("\n") + (1 if manifest and not manifest.endswith("\n") else 0)
    print(f"ok kustomize ({lines} lines)")

    print("==> image pins")
    verify_image_pins(manifest)
    print("ok")

    print("==> terraform validate (local env, no apply)")
    terraform_validate()
    print("ok")

    print("prodavan-ops validate OK")
