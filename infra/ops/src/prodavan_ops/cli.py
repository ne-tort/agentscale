from __future__ import annotations

import typer

from prodavan_ops import __version__
from prodavan_ops.dns_check import REQUIRED_HOSTS, check_dns
from prodavan_ops.e2e import cleanup_e2e, run_e2e
from prodavan_ops.heal import heal_cluster
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
    app_name: str = typer.Option("agentscale-dev", "--app"),
    namespace: str = typer.Option("argocd", "--namespace"),
    timeout: int = typer.Option(600, "--timeout"),
) -> None:
    """Wait for Argo Application Synced+Healthy (refresh annotation only)."""
    assert_kubeconfig_docker_ready()
    wait_argo_app(name=app_name, namespace=namespace, timeout_sec=timeout)


@app.command("rollout")
def rollout_cmd(
    namespace: str | None = typer.Option(
        None,
        "--namespace",
        help="Override: rollout only this namespace (default: all first-party targets).",
    ),
    timeout: int = typer.Option(600, "--timeout"),
) -> None:
    """Restart first-party Deployments so :latest+Always re-pulls after CI Images.

    By default restarts all DEPLOYMENT_TARGETS (agentscale-dev: api/web/
    celery-worker/celery-beat/celery-indexer; agentscale-dev-sandboxes:
    agentscale-probe-pod). Pass --namespace to restrict to a single namespace's
    first-party deployments.
    """
    assert_kubeconfig_docker_ready()
    if namespace is not None:
        rollout_restart(namespace=namespace, deployments=FIRST_PARTY_DEPLOYMENTS, timeout_sec=timeout)
    else:
        rollout_restart(timeout_sec=timeout)


@app.command("smoke")
def smoke_cmd(
    addr: str | None = typer.Option(None, "--addr"),
    port: int | None = typer.Option(None, "--port"),
    host: str = typer.Option("localhost", "--host"),
) -> None:
    smoke(addr=addr, port=port, host_header=host)


@app.command("dns-check")
def dns_check_cmd(
    namespace: str = typer.Option("agentscale", "--namespace", help="Namespace с Running-подом"),
    pod: str | None = typer.Option(None, "--pod", help="Pod[/container]; по умолчанию первый Running"),
    host: list[str] = typer.Option(
        None, "--host", help="Имя для проверки (можно несколько); по умолчанию github.com"
    ),
    max_coredns_timeouts: int = typer.Option(20, "--max-coredns-timeouts"),
) -> None:
    """Резолвит внешние имена ИЗ ПОДА и проверяет поток таймаутов CoreDNS.

    Отдельная команда, потому что такой отказ не виден ни в одной другой пробе:
    поды Running, HTTP-пробы зелёные, TCP-egress работает — а DNS мёртв.
    Инцидент 2026-10-22 на prod: CoreDNS форвардил на нерабочий upstream из
    /etc/resolv.conf, агент не мог достучаться до провайдера ИИ.
    """
    hosts = tuple(host) if host else REQUIRED_HOSTS
    resolved = check_dns(
        namespace=namespace,
        pod=pod,
        hosts=hosts,
        max_coredns_timeouts=max_coredns_timeouts,
    )
    typer.echo(f"dns OK: {resolved}")


@app.command("heal")
def heal_cmd(
    wait_node: int = typer.Option(180, "--wait-node", help="Seconds to wait for node Ready"),
    smoke_attempts: int = typer.Option(24, "--smoke-attempts"),
    skip_traefik: bool = typer.Option(False, "--skip-traefik"),
    skip_smoke: bool = typer.Option(False, "--skip-smoke"),
    skip_dns: bool = typer.Option(False, "--skip-dns"),
) -> None:
    """Post-reboot recovery: delete stuck pods, recycle Traefik, check DNS, smoke :8088."""
    assert_kubeconfig_docker_ready()
    heal_cluster(
        wait_node_sec=wait_node,
        smoke_attempts=smoke_attempts,
        skip_traefik=skip_traefik,
        skip_smoke=skip_smoke,
        skip_dns=skip_dns,
    )


e2e_app = typer.Typer(help="Cluster e2e runner (agentscale-e2e Argo app + Job).")
app.add_typer(e2e_app, name="e2e")


@e2e_app.command("run")
def e2e_run_cmd(
    suite: str = typer.Option("k8s", "--suite", help="k8s | all"),
    timeout: int = typer.Option(900, "--timeout"),
) -> None:
    """Sync agentscale-e2e, wait pytest Job, print logs on failure."""
    assert_kubeconfig_docker_ready()
    run_e2e(suite=suite, timeout_sec=timeout)


@e2e_app.command("cleanup")
def e2e_cleanup_cmd(
    unsync: bool = typer.Option(False, "--unsync", help="Delete Argo Application agentscale-e2e"),
) -> None:
    """Remove e2e Job (and optionally the Argo app)."""
    assert_kubeconfig_docker_ready()
    cleanup_e2e(unsync=unsync)


if __name__ == "__main__":
    app()
