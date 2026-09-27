"""AgentSandboxPodRuntimeAdapter unit tests (Wave 2).

The SDK (k8s-agent-sandbox) is NOT a test dependency: a stub module tree is
injected into sys.modules BEFORE importing the adapter, mirroring the
sandbox_client.py import guard. The SDK client itself is an AsyncMock/MagicMock
following the repo's mock style.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# --- SDK stub in sys.modules (must exist before importing the adapter) ------
_sdk = types.ModuleType("k8s_agent_sandbox")


class _FakeSandboxNotFoundError(RuntimeError):
    # ``status = 404`` mirrors kubernetes_asyncio.ApiException: the adapter's
    # _is_not_found recognizes it even when the REAL SDK is installed (CI) and
    # its SandboxNotFoundError class — not this fake — is bound in the adapter.
    status = 404


_sdk_exc = types.ModuleType("k8s_agent_sandbox.exceptions")
_sdk_exc.SandboxNotFoundError = _FakeSandboxNotFoundError
_sdk.exceptions = _sdk_exc
if "k8s_agent_sandbox" not in sys.modules:
    sys.modules["k8s_agent_sandbox"] = _sdk
    sys.modules["k8s_agent_sandbox.exceptions"] = _sdk_exc

from prodavan.application.pod_service.adapters.agent_sandbox.runtime import (  # noqa: E402
    AgentSandboxPodRuntimeAdapter,
    SandboxRuntimeUnavailableError,
    sandbox_claim_ref,
)
from prodavan.domain.pods.context import PodRuntimeContext  # noqa: E402

NAMESPACE = "prodavan-sandboxes"
REF = "sandbox-claim-ws-demo-01"
SANDBOX_NAME = "sbx-abc123"


def _context(**over: Any) -> PodRuntimeContext:
    base = dict(
        pod_id="pod_123",
        project_id="prj_1",
        company_id="cmp_1",
        workspace_key="ws-demo-01",
    )
    base.update(over)
    return PodRuntimeContext(**base)


def _claim(
    *,
    labels: dict[str, str] | None = None,
    ready: tuple[str, str] | None = None,
    sandbox_name: str | None = SANDBOX_NAME,
    service_fqdn: str | None = "sbx-abc123.prodavan-sandboxes.svc.cluster.local",
) -> dict[str, Any]:
    conditions = []
    if ready is not None:
        status, reason = ready
        conditions.append({"type": "Ready", "status": status, "reason": reason})
    sandbox_status: dict[str, Any] = {}
    if sandbox_name:
        sandbox_status["name"] = sandbox_name
    if service_fqdn:
        sandbox_status["serviceFQDN"] = service_fqdn
    return {
        "metadata": {
            "name": REF,
            "labels": labels
            or {
                "prodavan.io/pod-id": "pod_123",
                "prodavan.io/project-id": "prj_1",
                "prodavan.io/company-id": "cmp_1",
            },
        },
        "spec": {"warmPoolRef": {"name": "prodavan-agent-pool"}},
        "status": {"conditions": conditions, "sandbox": sandbox_status},
    }


def _sandbox(
    *,
    mode: str = "Running",
    ready: tuple[str, str] | None = ("True", "DependenciesReady"),
    launch_type: str | None = "warm",
    suspended: tuple[str, str] | None = None,
    pod_scheduled: tuple[str, str] | None = None,
) -> dict[str, Any]:
    conditions = []
    if ready is not None:
        status, reason = ready
        conditions.append({"type": "Ready", "status": status, "reason": reason})
    if suspended is not None:
        status, reason = suspended
        conditions.append({"type": "Suspended", "status": status, "reason": reason})
    if pod_scheduled is not None:
        status, reason = pod_scheduled
        conditions.append({"type": "PodScheduled", "status": status, "reason": reason})
    out: dict[str, Any] = {
        "metadata": {"name": SANDBOX_NAME, "labels": {}},
        "spec": {"operatingMode": mode},
        "status": {"conditions": conditions},
    }
    if launch_type:
        out["metadata"]["labels"]["agents.x-k8s.io/launch-type"] = launch_type
    return out


def _client(
    *,
    claim: dict[str, Any] | None = None,
    sandbox: dict[str, Any] | None = None,
) -> MagicMock:
    client = MagicMock()
    helper = MagicMock()
    helper.get_sandbox_claim = AsyncMock(return_value=claim)
    helper.get_sandbox = AsyncMock(return_value=sandbox)
    helper.create_sandbox_claim = AsyncMock(return_value={"metadata": {"name": REF}})
    helper.custom_objects_api = MagicMock()
    helper.custom_objects_api.patch_namespaced_custom_object = AsyncMock(
        return_value=None
    )
    client.k8s_helper = helper
    client.list_all_sandboxes = AsyncMock(return_value=[])
    client.delete_sandbox = AsyncMock(return_value=None)
    client.create_sandbox = AsyncMock(return_value=MagicMock())
    return client


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch):
    from prodavan.config.settings import settings

    monkeypatch.setattr(settings, "pod_runtime_mode", "sandbox")
    monkeypatch.setattr(settings, "pod_sandbox_namespace", NAMESPACE)
    monkeypatch.setattr(settings, "pod_sandbox_warmpool", "prodavan-agent-pool")
    monkeypatch.setattr(settings, "pod_sandbox_shutdown_ttl_sec", 604800)


# ---------------------------------------------------------------- ref naming


def test_sandbox_claim_ref_is_deterministic() -> None:
    assert sandbox_claim_ref("WS_Demo_01") == "sandbox-claim-ws-demo-01"


def test_sandbox_claim_ref_is_domain_single_source() -> None:
    # PodCommand persists runtime_ref_for(mode="sandbox"); the adapter must
    # resolve the SAME claim name or create/get diverge (k8s 422 on create
    # with an invalid name, 404 on get).
    from prodavan.domain.pods.types import runtime_ref_for

    assert sandbox_claim_ref("ws-demo-01") == runtime_ref_for("ws-demo-01", mode="sandbox")


def test_runtime_ref_for_sandbox_is_valid_claim_name() -> None:
    from prodavan.domain.pods.types import runtime_ref_for

    ref = runtime_ref_for("ws-demo-01", mode="sandbox")
    assert ref == "sandbox-claim-ws-demo-01"
    # k8s resource names must not contain ':' (the stub format does).
    assert ":" not in ref


# ------------------------------------------------------------ ensure_running


async def test_ensure_running_creates_claim_with_labels_and_ttl() -> None:
    client = _client(claim=None, sandbox=None)
    adapter = AgentSandboxPodRuntimeAdapter(client=client)
    await adapter.ensure_running(runtime_ref=REF, context=_context())
    client.k8s_helper.create_sandbox_claim.assert_awaited_once()
    args, kwargs = client.k8s_helper.create_sandbox_claim.await_args
    assert args[0] == REF
    assert args[1] == "prodavan-agent-pool"
    assert args[2] == NAMESPACE
    labels = kwargs["labels"]
    assert labels["prodavan.io/pod-id"] == "pod_123"
    assert labels["prodavan.io/project-id"] == "prj_1"
    assert labels["prodavan.io/company-id"] == "cmp_1"
    lifecycle = kwargs["lifecycle"]
    assert lifecycle is not None
    assert lifecycle["shutdownPolicy"] == "Delete"
    assert lifecycle["shutdownTime"]


async def test_ensure_running_idempotent_when_claim_ready() -> None:
    claim = _claim(ready=("True", "DependenciesReady"))
    sandbox = _sandbox(mode="Running", ready=("True", "DependenciesReady"))
    client = _client(claim=claim, sandbox=sandbox)
    adapter = AgentSandboxPodRuntimeAdapter(client=client)
    await adapter.ensure_running(runtime_ref=REF, context=_context())
    client.k8s_helper.create_sandbox_claim.assert_not_awaited()
    client.k8s_helper.custom_objects_api.patch_namespaced_custom_object.assert_not_awaited()


async def test_ensure_running_resumes_suspended_sandbox() -> None:
    claim = _claim(ready=("False", "SandboxSuspended"))
    sandbox = _sandbox(mode="Suspended", ready=("False", "SandboxSuspended"))
    client = _client(claim=claim, sandbox=sandbox)
    adapter = AgentSandboxPodRuntimeAdapter(client=client)
    await adapter.ensure_running(runtime_ref=REF, context=_context())
    patch = client.k8s_helper.custom_objects_api.patch_namespaced_custom_object
    patch.assert_awaited_once()
    kwargs = patch.await_args.kwargs
    assert kwargs["name"] == SANDBOX_NAME
    assert kwargs["namespace"] == NAMESPACE
    assert kwargs["plural"] == "sandboxes"
    assert kwargs["body"] == {"spec": {"operatingMode": "Running"}}
    client.k8s_helper.create_sandbox_claim.assert_not_awaited()


async def test_ensure_running_requires_client_when_none() -> None:
    import prodavan.core.infra.sandbox_client as sandbox_client_module

    adapter = AgentSandboxPodRuntimeAdapter()
    with pytest.raises(SandboxRuntimeUnavailableError):
        await adapter.ensure_running(runtime_ref=REF, context=_context())
    # manager absent entirely
    assert sandbox_client_module.get_sandbox_client_manager() is None


# --------------------------------------------------------------------- pause


async def test_pause_patches_operating_mode_suspended() -> None:
    claim = _claim(ready=("True", "DependenciesReady"))
    sandbox = _sandbox(mode="Running", ready=("True", "DependenciesReady"))
    client = _client(claim=claim, sandbox=sandbox)
    adapter = AgentSandboxPodRuntimeAdapter(client=client)
    await adapter.pause(runtime_ref=REF)
    patch = client.k8s_helper.custom_objects_api.patch_namespaced_custom_object
    patch.assert_awaited_once()
    assert patch.await_args.kwargs["body"] == {"spec": {"operatingMode": "Suspended"}}


async def test_pause_idempotent_when_already_suspended() -> None:
    claim = _claim(ready=("False", "SandboxSuspended"))
    sandbox = _sandbox(mode="Suspended", ready=("False", "SandboxSuspended"))
    client = _client(claim=claim, sandbox=sandbox)
    adapter = AgentSandboxPodRuntimeAdapter(client=client)
    await adapter.pause(runtime_ref=REF)
    client.k8s_helper.custom_objects_api.patch_namespaced_custom_object.assert_not_awaited()


async def test_pause_noop_when_claim_absent() -> None:
    client = _client(claim=None, sandbox=None)
    adapter = AgentSandboxPodRuntimeAdapter(client=client)
    await adapter.pause(runtime_ref=REF)
    client.k8s_helper.custom_objects_api.patch_namespaced_custom_object.assert_not_awaited()


# ------------------------------------------------------------------ terminate


async def test_terminate_deletes_claim() -> None:
    client = _client(claim=_claim(), sandbox=_sandbox())
    adapter = AgentSandboxPodRuntimeAdapter(client=client)
    await adapter.terminate(runtime_ref=REF)
    client.delete_sandbox.assert_awaited_once_with(REF, namespace=NAMESPACE)


async def test_terminate_not_found_is_ok() -> None:
    client = _client(claim=None, sandbox=None)
    client.delete_sandbox = AsyncMock(side_effect=_FakeSandboxNotFoundError("gone"))
    adapter = AgentSandboxPodRuntimeAdapter(client=client)
    await adapter.terminate(runtime_ref=REF)  # must not raise


async def test_force_kill_deletes_claim() -> None:
    client = _client(claim=None, sandbox=None)
    adapter = AgentSandboxPodRuntimeAdapter(client=client)
    await adapter.force_kill(runtime_ref=REF)
    client.delete_sandbox.assert_awaited_once_with(REF, namespace=NAMESPACE)


# ------------------------------------------------------------------ get_status


async def test_get_status_absent() -> None:
    client = _client(claim=None, sandbox=None)
    adapter = AgentSandboxPodRuntimeAdapter(client=client)
    status = await adapter.get_status(runtime_ref=REF)
    assert status["observed_state"] == "absent"
    assert status["phase"] == "NotFound"
    assert status["runtime_ref"] == REF


async def test_get_status_running() -> None:
    claim = _claim(ready=("True", "DependenciesReady"))
    sandbox = _sandbox(mode="Running", ready=("True", "DependenciesReady"))
    client = _client(claim=claim, sandbox=sandbox)
    adapter = AgentSandboxPodRuntimeAdapter(client=client)
    status = await adapter.get_status(runtime_ref=REF)
    assert status["observed_state"] == "running"
    assert status["phase"] == "Running"
    assert status["ready"] is True
    assert status["suspended"] is False
    assert status["sandbox_name"] == SANDBOX_NAME
    assert status["service_fqdn"].endswith("svc.cluster.local")
    assert status["launch_type"] == "warm"
    assert status["pod_id"] == "pod_123"


async def test_get_status_suspended() -> None:
    claim = _claim(ready=("False", "SandboxSuspended"))
    sandbox = _sandbox(
        mode="Suspended",
        ready=("False", "SandboxSuspended"),
        suspended=("True", "PodTerminated"),
    )
    client = _client(claim=claim, sandbox=sandbox)
    adapter = AgentSandboxPodRuntimeAdapter(client=client)
    status = await adapter.get_status(runtime_ref=REF)
    assert status["observed_state"] == "suspended"
    assert status["suspended"] is True
    assert status["ready"] is False


async def test_get_status_pausing() -> None:
    claim = _claim(ready=("False", "DependenciesNotReady"))
    sandbox = _sandbox(
        mode="Suspended",
        ready=None,
        suspended=("False", "PodTerminating"),
    )
    client = _client(claim=claim, sandbox=sandbox)
    adapter = AgentSandboxPodRuntimeAdapter(client=client)
    status = await adapter.get_status(runtime_ref=REF)
    assert status["observed_state"] == "pausing"
    assert status["phase"] == "Terminating"


async def test_get_status_provisioning() -> None:
    claim = _claim(ready=None)
    sandbox = _sandbox(
        mode="Running",
        ready=None,
        pod_scheduled=("False", "Unschedulable"),
        launch_type="cold",
    )
    client = _client(claim=claim, sandbox=sandbox)
    adapter = AgentSandboxPodRuntimeAdapter(client=client)
    status = await adapter.get_status(runtime_ref=REF)
    assert status["observed_state"] == "provisioning"
    assert status["phase"] == "Pending"
    assert status["launch_type"] == "cold"


async def test_get_status_failed_reconciler_error() -> None:
    claim = _claim(ready=("False", "ReconcilerError"))
    sandbox = _sandbox(mode="Running", ready=("False", "ReconcilerError"))
    client = _client(claim=claim, sandbox=sandbox)
    adapter = AgentSandboxPodRuntimeAdapter(client=client)
    status = await adapter.get_status(runtime_ref=REF)
    assert status["observed_state"] == "failed"
    assert status["phase"] == "Failed"


async def test_get_status_failed_invalid_configuration() -> None:
    claim = _claim(ready=("False", "InvalidConfiguration"))
    sandbox = _sandbox(mode="Running", ready=("False", "InvalidConfiguration"))
    client = _client(claim=claim, sandbox=sandbox)
    adapter = AgentSandboxPodRuntimeAdapter(client=client)
    status = await adapter.get_status(runtime_ref=REF)
    assert status["observed_state"] == "failed"


async def test_get_status_provisioning_without_conditions() -> None:
    # Freshly created claim: no conditions, no sandbox yet.
    claim = _claim(ready=None, sandbox_name=None, service_fqdn=None)
    client = _client(claim=claim, sandbox=None)
    adapter = AgentSandboxPodRuntimeAdapter(client=client)
    status = await adapter.get_status(runtime_ref=REF)
    assert status["observed_state"] == "provisioning"


# ----------------------------------------------------------- list_managed_pods


async def test_list_managed_pods_uses_pod_id_selector() -> None:
    claim = _claim(ready=("True", "DependenciesReady"))
    sandbox = _sandbox(mode="Running", ready=("True", "DependenciesReady"))
    client = _client(claim=claim, sandbox=sandbox)
    client.list_all_sandboxes = AsyncMock(return_value=[REF])
    adapter = AgentSandboxPodRuntimeAdapter(client=client)
    pods = await adapter.list_managed_pods()
    client.list_all_sandboxes.assert_awaited_once_with(
        namespace=NAMESPACE, label_selector="prodavan.io/pod-id"
    )
    assert len(pods) == 1
    assert pods[0]["runtime_ref"] == REF
    assert pods[0]["pod_id"] == "pod_123"
    assert pods[0]["observed_state"] == "running"


# ------------------------------------------------------------------- factory


async def test_factory_builds_sandbox_singleton() -> None:
    import prodavan.application.pod_service.factory as factory_module

    factory_module._runtime_singleton = None
    first = factory_module.build_pod_runtime()
    assert isinstance(first, AgentSandboxPodRuntimeAdapter)
    # singleton behavior across calls
    again = factory_module.build_pod_runtime()
    assert again is first
    factory_module._runtime_singleton = None  # cleanup for other tests


def test_factory_hydrate_is_noop_in_sandbox_mode() -> None:
    import prodavan.application.pod_service.factory as factory_module
    from prodavan.application.pod_service.adapters.stub_hydrate import StubHydrateAdapter

    adapter = factory_module.build_hydrate()
    assert isinstance(adapter, StubHydrateAdapter)


def test_factory_dehydrate_streams_via_router_in_sandbox_mode() -> None:
    """Wave 3: sandbox dehydrate streams GET /v1/workspace/archive from the
    agent-runtime through the sandbox-router into the object store."""
    import prodavan.application.pod_service.factory as factory_module
    from prodavan.application.pod_service.adapters.agent_sandbox.dehydrate import (
        SandboxHttpDehydrateAdapter,
    )

    adapter = factory_module.build_dehydrate()
    assert isinstance(adapter, SandboxHttpDehydrateAdapter)
