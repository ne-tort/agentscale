from __future__ import annotations

import typer

from prodavan_ops import __version__
from prodavan_ops.k8s import assert_kubeconfig_docker_ready, rollout_restart, wait_argo_app
from prodavan_ops.smoke import smoke
from prodavan_ops.validate import validate_all

app = typer.Typer(
    name="prodavan-ops",
    help="GitOps companion: validate / wait / rollout / smoke. No apply, no cluster create, no secrets.",
    no_args_is_help=True,
)


@app.command("version")
def version_cmd() -> None:
    typer.echo(__version__)


@app.command("validate")
def validate_cmd() -> None:
    """Cluster-free: no .sh under infra/, kustomize render, image pins."""
    validate_all()


@app.command("wait")
def wait_cmd(
    app_name: str = typer.Option("prodavan-dev", "--app"),
    namespace: str = typer.Option("argocd", "--namespace"),
    timeout: int = typer.Option(600, "--timeout"),
) -> None:
    """Wait for Argo Application Synced+Healthy (refresh annotation only)."""
    assert_kubeconfig_docker_ready()
    wait_argo_app(name=app_name, namespace=namespace, timeout_sec=timeout)


@app.command("rollout")
def rollout_cmd(
    namespace: str = typer.Option("prodavan", "--namespace"),
    timeout: int = typer.Option(600, "--timeout"),
) -> None:
    """Restart first-party Deployments so :latest+Always re-pulls after CI Images."""
    assert_kubeconfig_docker_ready()
    rollout_restart(namespace=namespace, timeout_sec=timeout)


@app.command("smoke")
def smoke_cmd(
    addr: str | None = typer.Option(None, "--addr"),
    port: int | None = typer.Option(None, "--port"),
    host: str = typer.Option("localhost", "--host"),
) -> None:
    smoke(addr=addr, port=port, host_header=host)


if __name__ == "__main__":
    app()
