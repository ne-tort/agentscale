from __future__ import annotations

import json
from pathlib import Path

import typer

from prodavan_ops import __version__
from prodavan_ops.dns_check import REQUIRED_HOSTS, check_dns
from prodavan_ops.e2e import cleanup_e2e, run_e2e
from prodavan_ops.heal import heal_cluster
from prodavan_ops.k8s import (
    DEPLOYMENT_TARGETS,
    assert_kubeconfig_docker_ready,
    rollout_restart,
    wait_argo_app,
)
from prodavan_ops.plugin import (
    DEFAULT_MAX_PAYLOAD_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_PAYLOAD_BYTES,
    MAX_TIMEOUT_SECONDS,
    CommandResult,
    PluginAcceptanceError,
    execute_action,
    redact_data,
    write_evidence,
)
from prodavan_ops.smoke import smoke, smoke_public_https
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
    celery-worker/celery-beat/celery-indexer/searchnet; agentscale-dev-sandboxes:
    agentscale-probe-pod). Pass --namespace to restrict to that namespace's
    known targets — not to the agentscale-dev list, which would 404-skip in
    agentscale-dev-sandboxes and silently restart nothing.
    """
    assert_kubeconfig_docker_ready()
    if namespace is not None:
        targets = tuple(name for name, ns in DEPLOYMENT_TARGETS if ns == namespace)
        if not targets:
            raise typer.BadParameter(
                f"нет известных rollout-целей в namespace {namespace}; "
                f"известны: {sorted({ns for _n, ns in DEPLOYMENT_TARGETS})}",
                param_hint="--namespace",
            )
        rollout_restart(namespace=namespace, deployments=targets, timeout_sec=timeout)
    else:
        rollout_restart(timeout_sec=timeout)


@app.command("smoke")
def smoke_cmd(
    addr: str | None = typer.Option(None, "--addr"),
    port: int | None = typer.Option(None, "--port"),
    host: str = typer.Option("localhost", "--host"),
    public_host: str = typer.Option(
        "",
        "--public-host",
        help=(
            "Также проверить настоящий публичный путь: https://<host>/api/v1/auth/config. "
            "Плоский :8088 проверяет оболочку, но не TLS-роутер, на котором /api может "
            "пропасть (см. rollout в agentscale-api/deployment.yaml)."
        ),
    ),
) -> None:
    smoke(addr=addr, port=port, host_header=host)
    if public_host:
        smoke_public_https(host=public_host)


@app.command("dns-check")
def dns_check_cmd(
    namespace: str | None = typer.Option(
        None, "--namespace", help="Namespace с Running-подом (по умолчанию: dev, затем prod)"
    ),
    pod: str | None = typer.Option(None, "--pod", help="Pod[/container]; по умолчанию первый Running"),
    host: list[str] = typer.Option(
        None, "--host", help="Имя для проверки (можно несколько); по умолчанию github.com, auth.x.ai"
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
    """Post-reboot recovery: stuck pods, Traefik recycle, Ingress re-apply, DNS, smoke."""
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


plugin_app = typer.Typer(
    help=(
        "Plugin v4 live acceptance over HTTP. Credentials: "
        "PRODAVAN_PLUGIN_API_BASE_URL and exactly one of "
        "PRODAVAN_PLUGIN_BEARER_TOKEN / PRODAVAN_PLUGIN_SERVICE_TOKEN."
    ),
    no_args_is_help=True,
)
app.add_typer(plugin_app, name="plugin")


def _emit_plugin_result(result: CommandResult, *, evidence_path: Path | None = None) -> None:
    destination: Path | None = None
    if evidence_path is not None:
        try:
            destination = write_evidence(result, evidence_path)
        except PluginAcceptanceError as exc:
            document = {
                "status": "error",
                "operation": result.action,
                "request_id": result.request_id,
                "evidence_status": "failed",
                "error": exc.as_dict(),
            }
            typer.echo(
                json.dumps(
                    redact_data(document, sensitive_values=result.sensitive_values),
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                ),
                err=True,
            )
            raise typer.Exit(code=exc.exit_code) from exc
    document = result.console_document()
    if destination is not None:
        document["evidence_file"] = str(destination)
    typer.echo(
        json.dumps(
            redact_data(document, sensitive_values=result.sensitive_values),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        ),
        err=not result.ok,
    )
    if not result.ok:
        raise typer.Exit(code=result.exit_code)


def _run_plugin_action(
    action: str,
    *,
    company_id: str | None = None,
    operation: str | None = None,
    payload: Path | None = None,
    job_id: str | None = None,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    insecure: bool = False,
    retry: bool = False,
    evidence_path: Path | None = None,
) -> None:
    result = execute_action(
        action,
        company_id=company_id,
        operation=operation,
        payload_file=payload,
        job_id=job_id,
        max_payload_bytes=max_payload_bytes,
        timeout_seconds=timeout,
        insecure=insecure,
        retry=retry,
    )
    _emit_plugin_result(result, evidence_path=evidence_path)


def _plugin_timeout_option() -> float:
    return typer.Option(
        DEFAULT_TIMEOUT_SECONDS,
        "--timeout",
        min=0.1,
        max=MAX_TIMEOUT_SECONDS,
        help="Total per-attempt HTTP timeout in seconds (bounded).",
    )


def _plugin_insecure_option() -> bool:
    return typer.Option(
        False,
        "--insecure",
        help="Disable TLS verification only for an explicit self-signed dev endpoint; warns on stderr.",
    )


def _plugin_retry_option() -> bool:
    return typer.Option(
        False,
        "--retry",
        help=(
            "Retry once only after timeout/network failure or HTTP 502/503/504; "
            "invoke may repeat side effects (no retry storm)."
        ),
    )


def _plugin_payload_limit_option() -> int:
    return typer.Option(
        DEFAULT_MAX_PAYLOAD_BYTES,
        "--max-payload-bytes",
        min=1,
        max=MAX_PAYLOAD_BYTES,
        help=f"Maximum invoke JSON size in bytes (hard cap {MAX_PAYLOAD_BYTES}).",
    )


@plugin_app.command("status")
def plugin_status_cmd(
    company_id: str = typer.Option(..., "--company-id", help="Company whose installed modules are listed."),
    timeout: float = _plugin_timeout_option(),
    insecure: bool = _plugin_insecure_option(),
    retry: bool = _plugin_retry_option(),
) -> None:
    """Read installed modules and runtime/grant status for one company."""
    _run_plugin_action(
        "status", company_id=company_id, timeout=timeout, insecure=insecure, retry=retry
    )


@plugin_app.command("capabilities")
def plugin_capabilities_cmd(
    company_id: str = typer.Option(..., "--company-id", help="Company whose effective grants/quotas are read."),
    timeout: float = _plugin_timeout_option(),
    insecure: bool = _plugin_insecure_option(),
    retry: bool = _plugin_retry_option(),
) -> None:
    """Read effective grants/quotas; missing quotas produce incomplete evidence and non-zero exit."""
    _run_plugin_action(
        "capabilities", company_id=company_id, timeout=timeout, insecure=insecure, retry=retry
    )


@plugin_app.command("invoke")
def plugin_invoke_cmd(
    operation: str = typer.Option(
        ...,
        "--operation",
        "--capability",
        help="Closed typed operation, for example cache.get, docs.query, jobs.get or health.",
    ),
    payload: Path = typer.Option(..., "--payload", help="UTF-8 JSON object file; never a raw argv payload."),
    job_id: str | None = typer.Option(None, "--job-id", help="Typed path id for jobs.get/jobs.cancel."),
    max_payload_bytes: int = _plugin_payload_limit_option(),
    timeout: float = _plugin_timeout_option(),
    insecure: bool = _plugin_insecure_option(),
    retry: bool = _plugin_retry_option(),
) -> None:
    """Invoke a closed typed capability with a bounded JSON payload from a file."""
    _run_plugin_action(
        "invoke",
        operation=operation,
        payload=payload,
        job_id=job_id,
        max_payload_bytes=max_payload_bytes,
        timeout=timeout,
        insecure=insecure,
        retry=retry,
    )


@plugin_app.command("evidence")
def plugin_evidence_cmd(
    action: str = typer.Argument(..., help="Acceptance action: status, capabilities or invoke."),
    output: Path = typer.Option(
        ...,
        "--output",
        "-o",
        help="Exact external JSON evidence file; repository docs and missing parent dirs are rejected.",
    ),
    company_id: str | None = typer.Option(None, "--company-id", help="Required for status/capabilities."),
    operation: str | None = typer.Option(
        None, "--operation", "--capability", help="Required for invoke; closed typed operation."
    ),
    payload: Path | None = typer.Option(None, "--payload", help="Required for invoke: bounded JSON object file."),
    job_id: str | None = typer.Option(None, "--job-id", help="Typed path id for jobs.get/jobs.cancel."),
    max_payload_bytes: int = _plugin_payload_limit_option(),
    timeout: float = _plugin_timeout_option(),
    insecure: bool = _plugin_insecure_option(),
    retry: bool = _plugin_retry_option(),
) -> None:
    """Write sanitised request/response evidence even for failed or incomplete acceptance."""
    _run_plugin_action(
        action,
        company_id=company_id,
        operation=operation,
        payload=payload,
        job_id=job_id,
        max_payload_bytes=max_payload_bytes,
        timeout=timeout,
        insecure=insecure,
        retry=retry,
        evidence_path=output,
    )


if __name__ == "__main__":
    app()
