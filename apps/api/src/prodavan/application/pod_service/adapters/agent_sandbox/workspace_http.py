"""Sandbox workspace adapter — /v1/workspace/* through the sandbox-router.

In sandbox mode ``runtime_ref`` is a SandboxClaim name (``sandbox-claim-{ws}``),
not a Pod name, and the backing Sandbox (pod) is adopted from a warm pool and
can be replaced at any time — direct pod-IP addressing is impossible. All
traffic goes to the agent-sandbox sandbox-router with ``X-Sandbox-*`` selection
headers resolved from the claim's *current* Sandbox on every call (see
application/agent/runtime_transport.py).

Note: there is no kubectl-exec write fallback in sandbox mode — the runtime
image must expose the HTTP workspace API.
"""

from __future__ import annotations

import logging
from typing import Any

from prodavan.application.agent.runtime_transport import (
    RuntimeEndpoint,
    resolve_runtime_endpoint_for_ref,
)
from prodavan.application.pod_service.adapters.k8s.workspace_http import HttpWorkspaceAdapterBase
from prodavan.domain.errors import AppError

logger = logging.getLogger(__name__)


class SandboxHttpWorkspaceAdapter(HttpWorkspaceAdapterBase):
    """PodWorkspacePort for pod_runtime_mode=sandbox (router-addressed)."""

    def __init__(self, *, runtime: Any | None = None) -> None:
        # ``runtime``: PodRuntimePort-compatible object (AgentSandboxPodRuntimeAdapter).
        # None → resolved lazily from the factory singleton on first call.
        self._runtime = runtime

    async def _runtime_endpoint(self, runtime_ref: str) -> RuntimeEndpoint:
        endpoint = await resolve_runtime_endpoint_for_ref(runtime_ref, runtime=self._runtime)
        if endpoint is None:
            raise AppError(
                code="POD_NOT_RUNNING",
                title="Conflict",
                status=409,
                detail="pod is not running",
            )
        return endpoint
