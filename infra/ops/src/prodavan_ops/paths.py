from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    """infra/ops/src/prodavan_ops → repo root (prodavan/)."""
    return Path(__file__).resolve().parents[4]


def overlay_dev() -> Path:
    return repo_root() / "infra" / "k3s" / "overlays" / "dev"


def terraform_local() -> Path:
    return repo_root() / "infra" / "terraform" / "environments" / "local"


def default_kubeconfig() -> Path:
    return repo_root() / "infra" / ".kube" / "prodavan-k3d.yaml"


def k3d_config() -> Path:
    return repo_root() / "infra" / "k3d" / "prodavan-dev.yaml"


def argocd_apps() -> Path:
    return repo_root() / "infra" / "argocd" / "apps"
