"""Shared auth headers for API -> agent-runtime requests.

The agent-sandbox sandbox-router strips ``Authorization`` before forwarding
(credential isolation: the sandbox must not see the caller's Bearer). The
runtime therefore also accepts the shared bridge token in the
``X-Prodavan-Bridge-Token`` header, which the router forwards like any
custom header (prodavan-claw bridge ``auth.ts``).

``Authorization`` is still sent alongside so direct (non-router) addressing
and future router authz modes keep working.
"""

from __future__ import annotations

from prodavan.config.settings import settings

BRIDGE_TOKEN_HEADER = "X-Prodavan-Bridge-Token"


def runtime_auth_headers() -> dict[str, str]:
    """Auth headers for agent-runtime HTTP calls (empty when no token set)."""
    token = (settings.pod_agent_runtime_token or "").strip()
    if not token:
        return {}
    return {
        "Authorization": f"Bearer {token}",
        BRIDGE_TOKEN_HEADER: token,
    }
