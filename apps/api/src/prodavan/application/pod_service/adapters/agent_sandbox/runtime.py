"""AgentSandboxPodRuntimeAdapter — PodRuntimePort over kubernetes-sigs/agent-sandbox.

Wave 2 (feat/sandbox-runtime-adapter). Contract: PodRuntimePort
(application/pod_service/ports/pod_runtime.py). All cluster access goes
through the SDK client (``AsyncSandboxClient``) owned by
``core/infra/sandbox_client.py::SandboxClientResource`` — the adapter never
talks to the k8s API outside the SDK client object.

runtime_ref semantics (sandbox mode):
    ``sandbox-claim-{sanitize_dns(workspace_key)}``
The ref *is* the SandboxClaim name, which makes ensure_running a get-or-create
by name and pause/terminate address the same claim idempotently. The backing
Sandbox name differs (warm pool adoption) and is resolved from
``claim.status.sandbox.name`` on demand.

Pause semantics: patch the **Sandbox** ``spec.operatingMode=Suspended`` — the
controller terminates the Pod but keeps the Sandbox object and volumes
(resume = patch back to Running). Never delete.

Workspace files: PVC is the source of truth in sandbox mode (build_hydrate /
build_dehydrate are no-ops), so the adapter does not inject env or hydrate
hooks; ``PodRuntimeContext`` fields beyond identity labels are intentionally
unused here (see factory.py).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from prodavan.config.settings import settings
from prodavan.domain.pods.context import PodRuntimeContext

logger = logging.getLogger(__name__)

try:  # pragma: no cover - dependency guard (unit tests mock the manager)
    from k8s_agent_sandbox.exceptions import SandboxNotFoundError  # type: ignore[import-not-found]

    _SDK_IMPORT_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - k8s/stub modes must not break
    SandboxNotFoundError = None  # type: ignore[assignment]
    _SDK_IMPORT_ERROR = exc

# --- agent-sandbox API constants (mirrors k8s_agent_sandbox.constants) -----
SANDBOX_API_GROUP = "agents.x-k8s.io"
SANDBOX_API_VERSION = "v1beta1"
SANDBOX_PLURAL = "sandboxes"
LAUNCH_TYPE_LABEL = "agents.x-k8s.io/launch-type"
LAUNCH_TYPE_WARM = "warm"
LAUNCH_TYPE_COLD = "cold"

OPERATING_MODE_RUNNING = "Running"
OPERATING_MODE_SUSPENDED = "Suspended"

# --- prodavan labels stamped on the claim (and readable from listManaged) --
POD_ID_LABEL = "prodavan.io/pod-id"
PROJECT_ID_LABEL = "prodavan.io/project-id"
COMPANY_ID_LABEL = "prodavan.io/company-id"

CLAIM_PREFIX = "sandbox-claim-"

# Ready=False reasons that the claim/sandbox controller will not recover from.
_FAILED_READY_REASONS = frozenset(
    {
        "ReconcilerError",
        "InvalidConfiguration",
        "InvalidMetadata",
        "MultiplePods",
        "PodFailed",
        "PodSucceeded",
        "SandboxExpired",
        "ClaimExpired",
        "EnvVarsInjectionRejected",
        "VolumeClaimTemplatesError",
    }
)


class SandboxRuntimeUnavailableError(RuntimeError):
    """pod_runtime_mode=sandbox but the SDK client resource is not running."""


def sandbox_claim_ref(workspace_key: str) -> str:
    """Deterministic runtime_ref (SandboxClaim name) for sandbox mode.

    Delegates to ``domain.pods.types.runtime_ref_for`` so the claim name has
    a single source of truth (PodCommand persists the same value).
    """
    from prodavan.domain.pods.types import runtime_ref_for

    return runtime_ref_for(workspace_key, mode="sandbox")


def _condition(conditions: Any, ctype: str) -> dict[str, Any] | None:
    if not conditions:
        return None
    for c in conditions:
        if isinstance(c, dict) and c.get("type") == ctype:
            return c
    return None


def _conditions_of(obj: dict[str, Any] | None) -> list[dict[str, Any]]:
    return ((obj or {}).get("status") or {}).get("conditions") or []


class AgentSandboxPodRuntimeAdapter:
    """pod_service runtime backed by agent-sandbox SandboxClaims (warm pool)."""

    def __init__(self, client: Any | None = None) -> None:
        # ``client``: AsyncSandboxClient-compatible object. Production wiring
        # (factory.build_pod_runtime) leaves it None and resolves the client
        # lazily from the SandboxClientResource so the singleton survives
        # resource restarts. Tests inject mocks.
        self._client = client

    # ------------------------------------------------------------------ utils

    def _require_client(self) -> Any:
        if self._client is not None:
            return self._client
        from prodavan.core.infra.sandbox_client import get_sandbox_client_manager

        manager = get_sandbox_client_manager()
        client = manager.client if manager is not None else None
        if client is None:
            raise SandboxRuntimeUnavailableError(
                "pod_runtime_mode=sandbox but the sandbox SDK client is unavailable "
                "(SandboxClientResource not started or SDK disabled)"
            )
        return client

    @staticmethod
    def _is_not_found(exc: BaseException) -> bool:
        """True when exc carries 404 semantics (claim/sandbox absent)."""
        if SandboxNotFoundError is not None and isinstance(exc, SandboxNotFoundError):
            return True
        if type(exc).__name__ == "SandboxNotFoundError":
            return True
        return getattr(exc, "status", None) == 404

    def _namespace(self) -> str:
        return settings.pod_sandbox_namespace

    async def _get_claim(
        self, client: Any, claim_name: str, namespace: str
    ) -> dict[str, Any] | None:
        """Raw SandboxClaim dict via the SDK k8s helper; None when absent."""
        try:
            claim = await client.k8s_helper.get_sandbox_claim(claim_name, namespace)
        except Exception as exc:
            if not self._is_not_found(exc):
                raise
            return None
        return claim if isinstance(claim, dict) else None

    async def _get_sandbox(
        self, client: Any, sandbox_name: str, namespace: str
    ) -> dict[str, Any] | None:
        try:
            sandbox = await client.k8s_helper.get_sandbox(sandbox_name, namespace)
        except Exception as exc:
            if not self._is_not_found(exc):
                raise
            return None
        return sandbox if isinstance(sandbox, dict) else None

    async def _patch_sandbox_operating_mode(
        self, client: Any, sandbox_name: str, namespace: str, mode: str
    ) -> None:
        """Patch Sandbox spec.operatingMode (Suspend/Resume) via the SDK client."""
        body = {"spec": {"operatingMode": mode}}
        await client.k8s_helper.custom_objects_api.patch_namespaced_custom_object(
            group=SANDBOX_API_GROUP,
            version=SANDBOX_API_VERSION,
            namespace=namespace,
            plural=SANDBOX_PLURAL,
            name=sandbox_name,
            body=body,
        )
        logger.info("sandbox patched name=%s operatingMode=%s", sandbox_name, mode)

    @staticmethod
    def _sandbox_name_of(claim: dict[str, Any]) -> str | None:
        return ((claim.get("status") or {}).get("sandbox") or {}).get("name") or None

    async def _create_claim(
        self,
        client: Any,
        *,
        claim_name: str,
        namespace: str,
        context: PodRuntimeContext,
    ) -> None:
        """Create the SandboxClaim (deterministic name) with prodavan labels + TTL."""
        ttl = int(settings.pod_sandbox_shutdown_ttl_sec)
        lifecycle: dict[str, str] | None = None
        if ttl > 0:
            shutdown_time = datetime.now(UTC) + timedelta(seconds=ttl)
            lifecycle = {
                "shutdownTime": shutdown_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "shutdownPolicy": "Delete",
            }
        labels = {
            POD_ID_LABEL: context.pod_id,
            PROJECT_ID_LABEL: context.project_id,
            COMPANY_ID_LABEL: context.company_id,
        }
        await client.k8s_helper.create_sandbox_claim(
            claim_name,
            settings.pod_sandbox_warmpool,
            namespace,
            labels=labels,
            lifecycle=lifecycle,
        )
        logger.info(
            "sandbox claim created name=%s warmpool=%s ttl_sec=%s pod_id=%s",
            claim_name,
            settings.pod_sandbox_warmpool,
            ttl,
            context.pod_id,
        )

    # -------------------------------------------------------- PodRuntimePort

    async def ensure_running(
        self,
        *,
        runtime_ref: str,
        context: PodRuntimeContext,
    ) -> None:
        client = self._require_client()
        namespace = self._namespace()

        claim = await self._get_claim(client, runtime_ref, namespace)
        if claim is None:
            await self._create_claim(
                client, claim_name=runtime_ref, namespace=namespace, context=context
            )
            return

        # Idempotent resume: suspended sandbox → patch operatingMode back.
        sandbox_name = self._sandbox_name_of(claim)
        if sandbox_name:
            sandbox = await self._get_sandbox(client, sandbox_name, namespace)
            if sandbox is not None:
                mode = (sandbox.get("spec") or {}).get("operatingMode") or OPERATING_MODE_RUNNING
                if mode == OPERATING_MODE_SUSPENDED:
                    await self._patch_sandbox_operating_mode(
                        client, sandbox_name, namespace, OPERATING_MODE_RUNNING
                    )
                    return
        # Claim exists and is not suspended — provisioning/running is observed
        # asynchronously by runtime_observation; do not block the launch call.

    async def pause(self, *, runtime_ref: str) -> None:
        client = self._require_client()
        namespace = self._namespace()
        claim = await self._get_claim(client, runtime_ref, namespace)
        if claim is None:
            return
        sandbox_name = self._sandbox_name_of(claim)
        if not sandbox_name:
            # No sandbox bound yet (still provisioning) — nothing to suspend.
            return
        sandbox = await self._get_sandbox(client, sandbox_name, namespace)
        if sandbox is not None:
            mode = (sandbox.get("spec") or {}).get("operatingMode") or OPERATING_MODE_RUNNING
            if mode == OPERATING_MODE_SUSPENDED:
                return
        await self._patch_sandbox_operating_mode(
            client, sandbox_name, namespace, OPERATING_MODE_SUSPENDED
        )

    async def terminate(self, *, runtime_ref: str) -> None:
        client = self._require_client()
        namespace = self._namespace()
        try:
            await client.delete_sandbox(runtime_ref, namespace=namespace)
        except Exception as exc:
            if not self._is_not_found(exc):
                raise
            # NotFound — already gone; delete is idempotent success.

    async def force_kill(self, *, runtime_ref: str) -> None:
        # Same claim delete: the controller cascade tears the Sandbox + Pod.
        await self.terminate(runtime_ref=runtime_ref)

    async def get_status(self, *, runtime_ref: str) -> dict[str, Any]:
        client = self._require_client()
        namespace = self._namespace()
        out: dict[str, Any] = {
            "runtime_ref": runtime_ref,
            "phase": "NotFound",
            "stub": False,
            "observed_state": "absent",
            "ready": False,
            "suspended": False,
        }
        claim = await self._get_claim(client, runtime_ref, namespace)
        if claim is None:
            return out

        out["claim_name"] = runtime_ref
        labels = (claim.get("metadata") or {}).get("labels") or {}
        out["pod_id"] = labels.get(POD_ID_LABEL)
        out["project_id"] = labels.get(PROJECT_ID_LABEL)
        out["company_id"] = labels.get(COMPANY_ID_LABEL)

        claim_status = claim.get("status") or {}
        sandbox_status = claim_status.get("sandbox") or {}
        sandbox_name = sandbox_status.get("name") or None
        if sandbox_name:
            out["sandbox_name"] = sandbox_name
        if sandbox_status.get("serviceFQDN"):
            out["service_fqdn"] = sandbox_status["serviceFQDN"]

        sandbox: dict[str, Any] | None = None
        if sandbox_name:
            sandbox = await self._get_sandbox(client, sandbox_name, namespace)

        claim_ready = _condition(claim_status.get("conditions"), "Ready")
        sandbox_ready = _condition(_conditions_of(sandbox), "Ready")
        ready_cond = sandbox_ready or claim_ready
        spec_mode = (
            (sandbox or {}).get("spec", {}).get("operatingMode") or OPERATING_MODE_RUNNING
        )
        suspended = spec_mode == OPERATING_MODE_SUSPENDED
        out["suspended"] = suspended
        out["restarts"] = 0  # agent-sandbox pods are single-shot; no counter

        observed, phase, reason = self._derive_observed_state(
            suspended=suspended,
            ready_cond=ready_cond,
            sandbox=sandbox,
        )
        out["observed_state"] = observed
        out["phase"] = phase
        out["ready"] = bool(ready_cond is not None and ready_cond.get("status") == "True")
        if reason:
            out["waiting_reason"] = reason
        if sandbox is not None:
            launch_type = (
                (sandbox.get("metadata") or {}).get("labels") or {}
            ).get(LAUNCH_TYPE_LABEL)
            if launch_type in (LAUNCH_TYPE_WARM, LAUNCH_TYPE_COLD):
                out["launch_type"] = launch_type
        return out

    async def list_managed_pods(self) -> list[dict[str, Any]]:
        client = self._require_client()
        namespace = self._namespace()
        claims = await client.list_all_sandboxes(
            namespace=namespace, label_selector=POD_ID_LABEL
        )
        out: list[dict[str, Any]] = []
        for claim_name in claims:
            name = str(claim_name)
            try:
                status = await self.get_status(runtime_ref=name)
            except Exception:
                logger.exception("sandbox list: status failed claim=%s", name)
                continue
            out.append(status)
        return out

    # ------------------------------------------------------------- internals

    def _derive_observed_state(
        self,
        *,
        suspended: bool,
        ready_cond: dict[str, Any] | None,
        sandbox: dict[str, Any] | None,
    ) -> tuple[str, str, str | None]:
        """(observed_state, phase, reason) from sandbox/claim conditions."""
        if ready_cond is not None:
            reason = ready_cond.get("reason") or None
            if ready_cond.get("status") == "True":
                return "running", "Running", reason
            if reason == "SandboxSuspended":
                return "suspended", "Suspended", reason
            if reason in _FAILED_READY_REASONS:
                return "failed", "Failed", reason
            if suspended:
                # Suspension in progress (pod still terminating).
                return "pausing", "Terminating", reason
            return "provisioning", "Pending", reason

        if sandbox is not None:
            suspended_cond = _condition(_conditions_of(sandbox), "Suspended")
            if suspended_cond is not None:
                if suspended_cond.get("status") == "True":
                    return "suspended", "Suspended", suspended_cond.get("reason") or None
                if suspended_cond.get("reason") == "PodTerminating":
                    return "pausing", "Terminating", suspended_cond.get("reason")
            pod_scheduled = _condition(_conditions_of(sandbox), "PodScheduled")
            if pod_scheduled is not None and pod_scheduled.get("status") != "True":
                return "provisioning", "Pending", pod_scheduled.get("reason") or None
        # No definitive condition yet — claim was just created.
        return "provisioning", "Pending", None
