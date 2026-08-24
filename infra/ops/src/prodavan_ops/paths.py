from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    """infra/ops/src/prodavan_ops → repo root (prodavan/)."""
    return Path(__file__).resolve().parents[4]


def overlay_dev() -> Path:
    return repo_root() / "infra" / "k3s" / "overlays" / "dev"


def default_kubeconfig() -> Path:
    """Host kubeconfig (not in git). Prefer ~/.kube/prodavan-dev.yaml."""
    home = Path.home() / ".kube" / "prodavan-dev.yaml"
    if home.is_file():
        return home
    return Path.home() / ".kube" / "config"
