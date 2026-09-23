"""Pod runtime context passed to infrastructure adapters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PodRuntimeContext:
    pod_id: str
    project_id: str
    company_id: str
    workspace_key: str
    hydrate_generation: int = 0
    extra_env: tuple[tuple[str, str], ...] = ()
    # Per-pod Pod Identity Bridge JWT (injected as PRODAVAN_AUTH_TOKEN value).
    pod_auth_token: str | None = None
    # Bridge token generation (the `gen` claim). Tracked in the pod label so
    # that the runtime adapter can detect when a Pending/Running pod carries a
    # stale token (bumped after creation by pause/reload/terminate) and
    # recreate it with the freshly minted one. Without this, a resume after a
    # pause leaves the pod stuck in Init:Error with a revoked bridge token.
    pod_bridge_gen: int | None = None
