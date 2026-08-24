from __future__ import annotations

import typer

from prodavan_ops import __version__
from prodavan_ops.k8s import ensure_ghcr_pull_secret, wait_argo_app
from prodavan_ops.seed import seed
from prodavan_ops.smoke import smoke
from prodavan_ops.validate import validate_all

app = typer.Typer(
    name="prodavan-ops",
    help="GitOps companion CLI (Python-only). No cluster create / no kustomize apply.",
    no_args_is_help=True,
)


@app.command("version")
def version_cmd() -> None:
    typer.echo(__version__)


@app.command("validate")
def validate_cmd() -> None:
    """Cluster-free: no .sh, kustomize render, image pins, terraform validate."""
    validate_all()


@app.command("wait")
def wait_cmd(
    app_name: str = typer.Option("prodavan-dev", "--app"),
    namespace: str = typer.Option("argocd", "--namespace"),
    timeout: int = typer.Option(600, "--timeout"),
) -> None:
    """Wait for Argo Application Synced+Healthy (refresh annotation only)."""
    wait_argo_app(name=app_name, namespace=namespace, timeout_sec=timeout)


@app.command("smoke")
def smoke_cmd(
    addr: str | None = typer.Option(None, "--addr"),
    port: int | None = typer.Option(None, "--port"),
    host: str = typer.Option("prodavan.local", "--host"),
) -> None:
    smoke(addr=addr, port=port, host_header=host)


@app.command("seed")
def seed_cmd(
    skip_e2e: bool = typer.Option(False, "--skip-e2e"),
) -> None:
    seed(skip_e2e=skip_e2e)


@app.command("ensure-ghcr-secret")
def ensure_ghcr_secret_cmd() -> None:
    """One-time bootstrap of ghcr-pull from GHCR_TOKEN (secret not stored in git)."""
    ensure_ghcr_pull_secret()


if __name__ == "__main__":
    app()
