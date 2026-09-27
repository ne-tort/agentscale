"""Runtime endpoint resolution for API → agent-runtime data-path traffic.

Why a router: in ``pod_runtime_mode=sandbox`` the workload identity is a
SandboxClaim (``runtime_ref = sandbox-claim-{workspace_key}``), while the Pod
backing it is adopted from a warm pool and can be replaced at any time — the
Sandbox *name* (and pod IP) is unstable and must never be addressed directly.
The agent-sandbox sandbox-router HTTP gateway proxies requests to the
*current* Sandbox selected by ``X-Sandbox-Id`` / ``X-Sandbox-Namespace`` /
``X-Sandbox-Port`` headers, so the API resolves the live sandbox name from the
claim status and lets the router do the pod routing.

In ``k8s`` mode there is no router: the legacy direct pod-IP path is kept
(``http://{pod_ip}:{port}``). ``stub`` mode has no runtime at all → ``None``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from prodavan.config.settings import settings
from prodavan.infrastructure.k8s.sandbox.client import K8sSandboxClient

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeEndpoint:
    """Resolved agent-runtime address.

    ``base_url`` has no trailing slash; the caller appends the path.
    ``headers`` are merged ON TOP of the caller's auth headers (router
    selection metadata in sandbox mode, empty in k8s mode).
    """

    base_url: str
    headers: dict[str, str] = field(default_factory=dict)


def runtime_mode() -> str:
    return (settings.pod_runtime_mode or "stub").strip().lower()


async def resolve_runtime_ref(session: AsyncSession, project_id: str) -> str | None:
    """runtime_ref of the project's running pod (claim name in sandbox mode)."""
    from prodavan.application.pod_service.query import PodQuery

    view = await PodQuery(session).runtime_view(project_id)
    if view is None:
        return None
    if str(view.get("observed_state") or "") != "running":
        return None
    ref = str(view.get("k8s_pod_name") or view.get("runtime_ref") or "").strip()
    if not ref or ref.startswith("object-ws:"):
        return None
    return ref


async def resolve_runtime_endpoint(
    session: AsyncSession,
    project_id: str,
    *,
    k8s_client: K8sSandboxClient | None = None,
    runtime: Any | None = None,
) -> RuntimeEndpoint | None:
    """Endpoint for the project's running agent-runtime; None when not running."""
    runtime_ref = await resolve_runtime_ref(session, project_id)
    if not runtime_ref:
        return None
    return await resolve_runtime_endpoint_for_ref(
        runtime_ref,
        k8s_client=k8s_client,
        runtime=runtime,
    )


async def resolve_runtime_endpoint_for_ref(
    runtime_ref: str,
    *,
    k8s_client: K8sSandboxClient | None = None,
    runtime: Any | None = None,
) -> RuntimeEndpoint | None:
    """Endpoint for a known runtime_ref (no DB lookup) — mode-dispatched."""
    mode = runtime_mode()
    if mode == "sandbox":
        return await _sandbox_endpoint(runtime_ref, runtime=runtime)
    if mode == "k8s":
        return await _k8s_endpoint(runtime_ref, k8s_client=k8s_client)
    return None


async def _sandbox_endpoint(
    runtime_ref: str,
    *,
    runtime: Any | None,
) -> RuntimeEndpoint | None:
    if runtime is None:
        from prodavan.application.pod_service.factory import build_pod_runtime

        try:
            runtime = build_pod_runtime()
        except Exception as exc:
            logger.warning("runtime endpoint: sandbox runtime unavailable: %s", exc)
            return None
    try:
        status = await runtime.get_status(runtime_ref=runtime_ref)
    except Exception as exc:
        logger.warning(
            "runtime endpoint: sandbox status failed runtime_ref=%s: %s",
            runtime_ref,
            exc,
        )
        return None
    # The claim is adopted asynchronously; until status.sandbox.name is set the
    # router cannot route anywhere (claim name != sandbox name).
    sandbox_name = str((status or {}).get("sandbox_name") or "").strip()
    if not sandbox_name:
        return None
    router = (settings.pod_sandbox_router_url or "").strip().rstrip("/")
    if not router:
        return None
    return RuntimeEndpoint(
        base_url=router,
        headers={
            "X-Sandbox-Id": sandbox_name,
            "X-Sandbox-Namespace": settings.pod_sandbox_namespace,
            "X-Sandbox-Port": str(settings.pod_agent_runtime_port),
        },
    )


async def _k8s_endpoint(
    runtime_ref: str,
    *,
    k8s_client: K8sSandboxClient | None,
) -> RuntimeEndpoint | None:
    client = k8s_client or K8sSandboxClient(namespace=settings.pod_sandbox_namespace)
    if not client.available():
        return None
    snap = await client.get_pod(runtime_ref)
    if snap is None or not snap.ready or snap.phase != "Running" or not snap.pod_ip:
        return None
    return RuntimeEndpoint(
        base_url=f"http://{snap.pod_ip}:{settings.pod_agent_runtime_port}",
        headers={},
    )
